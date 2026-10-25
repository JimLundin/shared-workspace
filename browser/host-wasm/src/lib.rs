//! Browser bindings around the same signed workspace model as native hosts.

#[path = "../../../native/core/src/model.rs"]
mod model;

mod domain;
pub use domain::Domain;

#[cfg(target_arch = "wasm32")]
pub mod transport;
