//! A browser-owned Iroh endpoint. Fresh identities and synthetic bytes only.

use std::cell::RefCell;
use std::collections::VecDeque;
use std::rc::Rc;
use std::time::Duration;

use iroh::{Endpoint, EndpointAddr, EndpointId, endpoint::presets};
use n0_future::time::timeout;
use serde::Serialize;
use wasm_bindgen::prelude::*;
use wasm_bindgen_futures::spawn_local;

const ALPN: &[u8] = b"browser-iroh-probe/1";
const MAX_BYTES: usize = 64 * 1024;
const DEADLINE: Duration = Duration::from_secs(30);

#[derive(Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum Event {
    Received { peer_id: String, text: String },
    Rejected { peer_id: String },
    Failed { detail: String },
}

#[derive(Default)]
struct State {
    allowed_peer: Option<EndpointId>,
    events: VecDeque<Event>,
}

impl State {
    fn push(&mut self, event: Event) {
        if self.events.len() == 32 {
            self.events.pop_front();
        }
        self.events.push_back(event);
    }
}

#[derive(Serialize)]
struct Receipt {
    peer_id: String,
    text: String,
    bytes: usize,
}

#[wasm_bindgen]
pub struct BrowserPeer {
    endpoint: Endpoint,
    state: Rc<RefCell<State>>,
}

#[wasm_bindgen]
impl BrowserPeer {
    pub async fn start() -> Result<BrowserPeer, JsError> {
        console_error_panic_hook::set_once();
        let endpoint = Endpoint::builder(presets::N0)
            .alpns(vec![ALPN.to_vec()])
            .bind()
            .await
            .map_err(js_error)?;
        let state = Rc::new(RefCell::new(State::default()));
        let listener = endpoint.clone();
        let observed = state.clone();
        spawn_local(async move {
            while let Some(incoming) = listener.accept().await {
                let observed = observed.clone();
                spawn_local(async move {
                    let result = timeout(DEADLINE, async {
                        let connection = incoming.await.map_err(js_error)?;
                        let peer_id = connection.remote_id();
                        if observed.borrow().allowed_peer != Some(peer_id) {
                            observed.borrow_mut().push(Event::Rejected {
                                peer_id: peer_id.to_string(),
                            });
                            connection.close(1u32.into(), b"peer is not selected");
                            return Ok::<(), JsError>(());
                        }
                        let (mut send, mut receive) =
                            connection.accept_bi().await.map_err(js_error)?;
                        let bytes = receive.read_to_end(MAX_BYTES).await.map_err(js_error)?;
                        let text = String::from_utf8(bytes.clone()).map_err(js_error)?;
                        observed.borrow_mut().push(Event::Received {
                            peer_id: peer_id.to_string(),
                            text,
                        });
                        send.write_all(&bytes).await.map_err(js_error)?;
                        send.finish().map_err(js_error)?;
                        connection.closed().await;
                        Ok(())
                    })
                    .await;
                    match result {
                        Ok(Ok(())) => {}
                        Ok(Err(_)) => observed.borrow_mut().push(Event::Failed {
                            detail: "incoming exchange failed".into(),
                        }),
                        Err(_) => observed.borrow_mut().push(Event::Failed {
                            detail: "incoming exchange timed out".into(),
                        }),
                    }
                });
            }
        });
        Ok(Self { endpoint, state })
    }

    pub fn id(&self) -> String {
        self.endpoint.id().to_string()
    }

    pub async fn address(&self) -> Result<String, JsError> {
        timeout(DEADLINE, self.endpoint.online())
            .await
            .map_err(js_error)?;
        serde_json::to_string(&self.endpoint.addr()).map_err(js_error)
    }

    pub fn select_peer(&self, peer_id: String) -> Result<(), JsError> {
        let peer_id: EndpointId = peer_id.parse().map_err(js_error)?;
        if peer_id == self.endpoint.id() {
            return Err(JsError::new("select another endpoint"));
        }
        self.state.borrow_mut().allowed_peer = Some(peer_id);
        Ok(())
    }

    pub async fn send(&self, address_json: String, text: String) -> Result<String, JsError> {
        if address_json.len() > 4096 || text.is_empty() || text.len() > MAX_BYTES {
            return Err(JsError::new("invalid address or payload size"));
        }
        let address: EndpointAddr = serde_json::from_str(&address_json).map_err(js_error)?;
        if self.state.borrow().allowed_peer != Some(address.id) {
            return Err(JsError::new("address does not match selected peer"));
        }
        timeout(DEADLINE, async {
            let connection = self
                .endpoint
                .connect(address.clone(), ALPN)
                .await
                .map_err(js_error)?;
            if connection.remote_id() != address.id {
                return Err(JsError::new("authenticated peer identity mismatch"));
            }
            let result = async {
                let (mut send, mut receive) = connection.open_bi().await.map_err(js_error)?;
                send.write_all(text.as_bytes()).await.map_err(js_error)?;
                send.finish().map_err(js_error)?;
                let response = receive.read_to_end(MAX_BYTES).await.map_err(js_error)?;
                if response != text.as_bytes() {
                    return Err(JsError::new("response did not match sent bytes"));
                }
                serde_json::to_string(&Receipt {
                    peer_id: connection.remote_id().to_string(),
                    bytes: response.len(),
                    text,
                })
                .map_err(js_error)
            }
            .await;
            connection.close(0u32.into(), b"exchange complete");
            result
        })
        .await
        .map_err(js_error)?
    }

    pub fn take_events(&self) -> Result<String, JsError> {
        let events: Vec<_> = self.state.borrow_mut().events.drain(..).collect();
        serde_json::to_string(&events).map_err(js_error)
    }

    pub async fn close(&self) {
        self.endpoint.close().await;
    }
}

fn js_error(error: impl std::fmt::Display) -> JsError {
    JsError::new(&error.to_string())
}
