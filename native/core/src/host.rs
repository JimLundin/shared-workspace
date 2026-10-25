//! One native owner for local workspace commands, peer lifetime and observations.

use std::collections::BTreeMap;
use std::path::Path;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use tokio::sync::{Mutex, Notify};
use tokio::task::JoinHandle;

use crate::model::{Command, CommandReceipt, Snapshot};
use crate::peer::{NetworkMode, PairingInvite, PeerClient, RunningPeer, now_ms};
use crate::store::Store;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum NetworkState {
    Active,
    Paused,
    Closed,
    Unavailable { detail: String },
}

#[cfg(test)]
mod tests {
    use std::future::Future;
    use std::task::Poll;

    use super::*;

    #[tokio::test]
    async fn background_wake_cannot_reverse_an_explicit_pause() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let host = Host::open(directory.path(), "Laptop".into(), NetworkMode::Loopback).await?;
        // Hold the lifecycle owner so a user pause is ahead of the background
        // wake. Both operations still exercise the real endpoint and store.
        let lifecycle = host.lifecycle.lock().await;
        let pause = host.set_foreground(false);
        tokio::pin!(pause);
        std::future::poll_fn(|cx| {
            assert!(pause.as_mut().poll(cx).is_pending());
            Poll::Ready(())
        })
        .await;
        host.command(Command::CreateWorkspace {
            operation_id: "wake-background".into(),
            workspace_id: "work".into(),
            title: "Work".into(),
        })
        .await?;
        tokio::task::yield_now().await;
        drop(lifecycle);
        pause.await?;
        // Queue behind the maintenance turn before observing its result.
        drop(host.lifecycle.lock().await);
        assert!(matches!(
            host.snapshot().await?.network,
            NetworkState::Paused
        ));
        assert!(host.create_invite(vec!["work".into()], 60).await.is_err());
        host.shutdown().await?;
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum SyncState {
    NeverSynced,
    Syncing,
    Synced { at_ms: i64, imported: usize },
    Unreachable { at_ms: i64, detail: String },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PeerStatus {
    pub id: String,
    pub name: String,
    pub workspace_ids: Vec<String>,
    pub state: SyncState,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HostSnapshot {
    #[serde(flatten)]
    pub data: Snapshot,
    pub network: NetworkState,
    pub peers: Vec<PeerStatus>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SyncReport {
    pub peers: Vec<PeerStatus>,
}

pub struct Host {
    store: Store,
    mode: NetworkMode,
    running: Mutex<Option<RunningPeer>>,
    network_error: Mutex<Option<String>>,
    lifecycle: Mutex<()>,
    sync_lock: Mutex<()>,
    observations: Mutex<BTreeMap<String, SyncState>>,
    foreground: AtomicBool,
    closed: Arc<AtomicBool>,
    changed: Arc<Notify>,
    background: Mutex<Option<JoinHandle<()>>>,
}

impl Host {
    pub async fn open(
        directory: impl AsRef<Path>,
        device_name: String,
        mode: NetworkMode,
    ) -> Result<Arc<Self>> {
        let store = Store::open(directory, &device_name).await?;
        let (running, network_error) = match RunningPeer::start(store.clone(), mode).await {
            Ok(peer) => (Some(peer), None),
            Err(error) => (None, Some(error.to_string())),
        };
        let host = Arc::new(Self {
            store,
            mode,
            running: Mutex::new(running),
            network_error: Mutex::new(network_error),
            lifecycle: Mutex::new(()),
            sync_lock: Mutex::new(()),
            observations: Mutex::new(BTreeMap::new()),
            foreground: AtomicBool::new(true),
            closed: Arc::new(AtomicBool::new(false)),
            changed: Arc::new(Notify::new()),
            background: Mutex::new(None),
        });
        let weak = Arc::downgrade(&host);
        let changed = host.changed.clone();
        let closed = host.closed.clone();
        let background = tokio::spawn(async move {
            let mut interval = tokio::time::interval(Duration::from_secs(5));
            interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
            loop {
                if closed.load(Ordering::Acquire) {
                    break;
                }
                tokio::select! { _ = interval.tick() => {}, _ = changed.notified() => {} }
                if closed.load(Ordering::Acquire) {
                    break;
                }
                let Some(host) = weak.upgrade() else {
                    break;
                };
                if host.foreground.load(Ordering::Acquire) {
                    // Errors stay visible in peer observations; durable local work remains.
                    let _ = host.maintain_network().await;
                    let _ = host.sync().await;
                }
            }
        });
        *host.background.lock().await = Some(background);
        Ok(host)
    }

    pub async fn command(&self, command: Command) -> Result<CommandReceipt> {
        ensure!(!self.closed.load(Ordering::Acquire), "host is closed");
        let result = self.store.command(command).await?;
        self.changed.notify_one();
        Ok(result)
    }

    pub async fn snapshot(&self) -> Result<HostSnapshot> {
        let network = if self.closed.load(Ordering::Acquire) {
            NetworkState::Closed
        } else if !self.foreground.load(Ordering::Acquire) {
            NetworkState::Paused
        } else if let Some(detail) = self.network_error.lock().await.clone() {
            NetworkState::Unavailable { detail }
        } else {
            NetworkState::Active
        };
        Ok(HostSnapshot {
            data: self.store.snapshot().await?,
            network,
            peers: self.peer_statuses().await?,
        })
    }

    pub async fn create_invite(
        &self,
        workspace_ids: Vec<String>,
        ttl_seconds: u32,
    ) -> Result<PairingInvite> {
        self.client()
            .await?
            .invite(&self.store, workspace_ids, ttl_seconds)
            .await
    }

    pub async fn join(&self, ticket: String) -> Result<HostSnapshot> {
        self.client().await?.join(&self.store, &ticket).await?;
        self.changed.notify_one();
        self.snapshot().await
    }

    pub async fn sync(&self) -> Result<SyncReport> {
        let _serial = self.sync_lock.lock().await;
        let client = self.client().await?;
        for peer in self.store.peers().await? {
            if !self.foreground.load(Ordering::Acquire) || self.closed.load(Ordering::Acquire) {
                break;
            }
            self.observations
                .lock()
                .await
                .insert(peer.id.clone(), SyncState::Syncing);
            let state = match client.synchronize(&self.store, peer.clone()).await {
                Ok(receipt) => SyncState::Synced {
                    at_ms: now_ms()?,
                    imported: receipt.imported,
                },
                Err(error) => SyncState::Unreachable {
                    at_ms: now_ms()?,
                    detail: error
                        .to_string()
                        .chars()
                        .filter(|ch| !ch.is_control())
                        .take(240)
                        .collect(),
                },
            };
            self.observations.lock().await.insert(peer.id, state);
        }
        Ok(SyncReport {
            peers: self.peer_statuses().await?,
        })
    }

    pub async fn set_foreground(&self, foreground: bool) -> Result<()> {
        let _serial = self.lifecycle.lock().await;
        ensure!(!self.closed.load(Ordering::Acquire), "host is closed");
        if foreground == self.foreground.load(Ordering::Acquire)
            && (!foreground || self.running.lock().await.is_some())
        {
            return Ok(());
        }
        if foreground {
            self.foreground.store(true, Ordering::Release);
            self.start_network().await?;
            self.changed.notify_one();
        } else {
            self.foreground.store(false, Ordering::Release);
            let peer = self.running.lock().await.take();
            if let Some(peer) = peer {
                peer.shutdown().await?;
            }
        }
        Ok(())
    }

    async fn maintain_network(&self) -> Result<()> {
        let _serial = self.lifecycle.lock().await;
        // Recovery observes the user's desired state under the same owner as
        // pause/resume. An earlier foreground observation cannot undo a pause.
        if self.closed.load(Ordering::Acquire) || !self.foreground.load(Ordering::Acquire) {
            return Ok(());
        }
        if self.running.lock().await.is_none() {
            self.start_network().await?;
        }
        Ok(())
    }

    // The lifecycle owner serializes endpoint creation and shutdown.
    async fn start_network(&self) -> Result<()> {
        let peer = match RunningPeer::start(self.store.clone(), self.mode).await {
            Ok(peer) => peer,
            Err(error) => {
                *self.network_error.lock().await = Some(error.to_string());
                return Err(error);
            }
        };
        *self.running.lock().await = Some(peer);
        *self.network_error.lock().await = None;
        Ok(())
    }

    pub async fn shutdown(&self) -> Result<()> {
        let background = {
            let _serial = self.lifecycle.lock().await;
            if self.closed.swap(true, Ordering::AcqRel) {
                return Ok(());
            }
            self.foreground.store(false, Ordering::Release);
            self.changed.notify_one();
            let peer = self.running.lock().await.take();
            if let Some(peer) = peer {
                peer.shutdown().await?;
            }
            self.background.lock().await.take()
        };
        // A foreground retry may have been waiting for lifecycle ownership.
        // Release that lock before joining it so shutdown cannot deadlock.
        if let Some(background) = background {
            background.await?;
        }
        Ok(())
    }

    async fn client(&self) -> Result<PeerClient> {
        ensure!(!self.closed.load(Ordering::Acquire), "host is closed");
        let running = self.running.lock().await;
        match running.as_ref() {
            Some(peer) => Ok(peer.client()),
            None => anyhow::bail!(
                self.network_error
                    .lock()
                    .await
                    .clone()
                    .unwrap_or_else(|| "device networking is paused".into())
            ),
        }
    }

    async fn peer_statuses(&self) -> Result<Vec<PeerStatus>> {
        let peers = self.store.peers().await?;
        let states = self.observations.lock().await.clone();
        let mut result = Vec::with_capacity(peers.len());
        for peer in peers {
            result.push(PeerStatus {
                workspace_ids: self.store.allowed_workspaces(&peer.id).await?,
                state: states
                    .get(&peer.id)
                    .cloned()
                    .unwrap_or(SyncState::NeverSynced),
                id: peer.id,
                name: peer.name,
            });
        }
        Ok(result)
    }
}
