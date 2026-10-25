//! Browser-owned authenticated byte transport. The host owns workspace authority.

use std::cell::RefCell;
use std::collections::BTreeMap;
use std::future::Future;
use std::rc::Rc;
use std::time::Duration;

use futures_util::future::{AbortHandle, Abortable};
use iroh::endpoint::{Connection, Incoming, presets};
use iroh::{Endpoint, EndpointAddr, EndpointId, RelayMode, SecretKey};
use js_sys::{Function, Promise};
use n0_future::time::timeout;
use serde::Serialize;
use wasm_bindgen::prelude::*;
use wasm_bindgen_futures::{JsFuture, future_to_promise, spawn_local};

const ALPN: &[u8] = b"shared-workspace/browser/1";
const MAX_MESSAGE_BYTES: usize = 8 * 1024 * 1024;
const MAX_ADDRESS_BYTES: usize = 4096;
const MAX_INCOMING: usize = 16;
const DEADLINE: Duration = Duration::from_secs(30);

#[derive(Default)]
struct Lifetime {
    closed: bool,
    next_task: u64,
    listener: Option<AbortHandle>,
    requests: BTreeMap<u64, AbortHandle>,
    outgoing: BTreeMap<u64, AbortHandle>,
}

impl Lifetime {
    fn retire(&mut self) -> bool {
        if self.closed {
            return false;
        }
        self.closed = true;
        if let Some(listener) = self.listener.take() {
            listener.abort();
        }
        for (_, request) in std::mem::take(&mut self.requests) {
            request.abort();
        }
        for (_, operation) in std::mem::take(&mut self.outgoing) {
            operation.abort();
        }
        true
    }
}

struct OutgoingOperation {
    id: u64,
    lifetime: Rc<RefCell<Lifetime>>,
}

impl Drop for OutgoingOperation {
    fn drop(&mut self) {
        self.lifetime.borrow_mut().outgoing.remove(&self.id);
    }
}

fn owned_operation(
    lifetime: Rc<RefCell<Lifetime>>,
    operation: impl Future<Output = Result<JsValue, JsError>> + 'static,
) -> Promise {
    future_to_promise(async move {
        let (id, registration) = {
            let mut state = lifetime.borrow_mut();
            if state.closed {
                return Err(JsError::new("endpoint is closed").into());
            }
            let id = state.next_task;
            state.next_task += 1;
            let (abort, registration) = AbortHandle::new_pair();
            state.outgoing.insert(id, abort);
            (id, registration)
        };
        let _operation = OutgoingOperation { id, lifetime };
        match Abortable::new(operation, registration).await {
            Ok(result) => result.map_err(Into::into),
            Err(_) => Err(JsError::new("endpoint is closed").into()),
        }
    })
}

struct RequestConnection(Connection);

impl Drop for RequestConnection {
    fn drop(&mut self) {
        // Also runs when timeout, shutdown, or handler rejection drops the future.
        self.0.close(0u32.into(), b"request retired");
    }
}

#[derive(Serialize)]
struct Response {
    peer_id: String,
    payload: String,
}

#[wasm_bindgen]
pub struct BrowserEndpoint {
    endpoint: Endpoint,
    lifetime: Rc<RefCell<Lifetime>>,
}

#[wasm_bindgen]
impl BrowserEndpoint {
    pub async fn start(seed_hex: String, handler: Function) -> Result<BrowserEndpoint, JsError> {
        let mut seed = [0u8; 32];
        hex::decode_to_slice(seed_hex, &mut seed)
            .map_err(|_| JsError::new("device seed must be exactly 32 hexadecimal bytes"))?;
        let endpoint = Endpoint::builder(presets::N0)
            // WebKit rejects secure WebSockets to the default FQDN spelling
            // with a terminal DNS dot. Keep the same relays and TLS validation.
            .relay_mode(RelayMode::Custom(
                crate::browser_relay::default_relays().map_err(js_error)?,
            ))
            .secret_key(SecretKey::from_bytes(&seed))
            .alpns(vec![ALPN.to_vec()])
            .bind()
            .await
            .map_err(js_error)?;
        let (listener_abort, listener_registration) = AbortHandle::new_pair();
        let lifetime = Rc::new(RefCell::new(Lifetime {
            listener: Some(listener_abort),
            ..Lifetime::default()
        }));
        let receiver = endpoint.clone();
        let observed = lifetime.clone();
        spawn_local(async move {
            let _ = Abortable::new(
                async move {
                    while let Some(incoming) = receiver.accept().await {
                        let (task_id, registration) = {
                            let mut state = observed.borrow_mut();
                            if state.closed || state.requests.len() >= MAX_INCOMING {
                                incoming.refuse();
                                continue;
                            }
                            let id = state.next_task;
                            state.next_task += 1;
                            let (abort, registration) = AbortHandle::new_pair();
                            state.requests.insert(id, abort);
                            (id, registration)
                        };
                        let observed = observed.clone();
                        let handler = handler.clone();
                        spawn_local(async move {
                            // Handler errors are never logged or reflected as a success.
                            let _ = Abortable::new(
                                timeout(DEADLINE, receive(incoming, handler)),
                                registration,
                            )
                            .await;
                            observed.borrow_mut().requests.remove(&task_id);
                        });
                    }
                },
                listener_registration,
            )
            .await;
        });
        Ok(Self { endpoint, lifetime })
    }

    pub fn id(&self) -> String {
        self.endpoint.id().to_string()
    }

    #[wasm_bindgen(unchecked_return_type = "Promise<string>")]
    pub fn address(&self) -> Promise {
        let endpoint = self.endpoint.clone();
        owned_operation(self.lifetime.clone(), async move {
            timeout(DEADLINE, endpoint.online())
                .await
                .map_err(js_error)?;
            let address = serde_json::to_string(&endpoint.addr()).map_err(js_error)?;
            Ok(JsValue::from_str(&address))
        })
    }

    pub fn validate_address(
        address_json: String,
        expected_peer_id: String,
    ) -> Result<String, JsError> {
        let address = parse_address(&address_json)?;
        let expected: EndpointId = expected_peer_id.parse().map_err(js_error)?;
        if address.id != expected {
            return Err(JsError::new(
                "address does not match expected peer identity",
            ));
        }
        serde_json::to_string(&address).map_err(js_error)
    }

    #[wasm_bindgen(unchecked_return_type = "Promise<string>")]
    pub fn request(&self, address_json: String, payload: String) -> Promise {
        let endpoint = self.endpoint.clone();
        owned_operation(self.lifetime.clone(), async move {
            validate_payload(&payload)?;
            let address = parse_address(&address_json)?;
            if address.id == endpoint.id() {
                return Err(JsError::new("cannot request this endpoint itself"));
            }
            let response = timeout(DEADLINE, async {
                let connection = RequestConnection(
                    endpoint
                        .connect(address.clone(), ALPN)
                        .await
                        .map_err(js_error)?,
                );
                if connection.0.remote_id() != address.id {
                    return Err(JsError::new("authenticated peer identity mismatch"));
                }
                let (mut send, mut receive) = connection.0.open_bi().await.map_err(js_error)?;
                send.write_all(payload.as_bytes()).await.map_err(js_error)?;
                send.finish().map_err(js_error)?;
                let response = receive
                    .read_to_end(MAX_MESSAGE_BYTES)
                    .await
                    .map_err(js_error)?;
                let payload = String::from_utf8(response).map_err(js_error)?;
                validate_payload(&payload)?;
                serde_json::to_string(&Response {
                    peer_id: connection.0.remote_id().to_string(),
                    payload,
                })
                .map_err(js_error)
            })
            .await
            .map_err(js_error)??;
            Ok(JsValue::from_str(&response))
        })
    }

    #[wasm_bindgen(unchecked_return_type = "Promise<void>")]
    pub fn close(&self) -> Promise {
        self.lifetime.borrow_mut().retire();
        let endpoint = self.endpoint.clone();
        future_to_promise(async move {
            endpoint.close().await;
            Ok(JsValue::UNDEFINED)
        })
    }
}

impl Drop for BrowserEndpoint {
    fn drop(&mut self) {
        if self.lifetime.borrow_mut().retire() {
            let endpoint = self.endpoint.clone();
            spawn_local(async move { endpoint.close().await });
        }
    }
}

async fn receive(incoming: Incoming, handler: Function) -> Result<(), JsError> {
    let connection = RequestConnection(incoming.await.map_err(js_error)?);
    let peer_id = connection.0.remote_id().to_string();
    let (mut send, mut receive) = connection.0.accept_bi().await.map_err(js_error)?;
    let bytes = receive
        .read_to_end(MAX_MESSAGE_BYTES)
        .await
        .map_err(js_error)?;
    let payload = String::from_utf8(bytes).map_err(js_error)?;
    validate_payload(&payload)?;
    // Peer identity comes only from the authenticated Iroh connection.
    let pending = handler
        .call2(
            &JsValue::UNDEFINED,
            &JsValue::from_str(&peer_id),
            &JsValue::from_str(&payload),
        )
        .map_err(|_| JsError::new("request handler failed"))?;
    let value = JsFuture::from(Promise::resolve(&pending))
        .await
        .map_err(|_| JsError::new("request handler rejected"))?;
    let response = value
        .as_string()
        .ok_or_else(|| JsError::new("request handler must return a string"))?;
    validate_payload(&response)?;
    send.write_all(response.as_bytes())
        .await
        .map_err(js_error)?;
    send.finish().map_err(js_error)?;
    // Keep the response alive until the requester consumes it and closes.
    connection.0.closed().await;
    Ok(())
}

fn parse_address(encoded: &str) -> Result<EndpointAddr, JsError> {
    if encoded.len() > MAX_ADDRESS_BYTES {
        return Err(JsError::new("peer address exceeds 4 KiB"));
    }
    let address: EndpointAddr = serde_json::from_str(encoded).map_err(js_error)?;
    if address.addrs.len() > 16 {
        return Err(JsError::new("peer address has too many locators"));
    }
    // Previously issued desktop invitations may still carry the FQDN form.
    crate::browser_relay::peer_address(address).map_err(js_error)
}

fn validate_payload(payload: &str) -> Result<(), JsError> {
    if payload.is_empty() || payload.len() > MAX_MESSAGE_BYTES {
        return Err(JsError::new(
            "message must contain 1 byte to 8 MiB of UTF-8",
        ));
    }
    Ok(())
}

fn js_error(error: impl std::fmt::Display) -> JsError {
    JsError::new(&error.to_string())
}
