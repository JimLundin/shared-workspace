//! Line-oriented native peer for automation and independent-process verification.

use std::path::PathBuf;

use anyhow::{Context, Result, bail, ensure};
use serde::Deserialize;
use serde_json::{Value, json};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use workspace_core::{Command, Host, NetworkMode};

#[derive(Deserialize)]
struct Call {
    id: String,
    #[serde(flatten)]
    request: Method,
}

#[derive(Deserialize)]
#[serde(
    tag = "method",
    content = "params",
    rename_all = "snake_case",
    deny_unknown_fields
)]
enum Method {
    Snapshot,
    Command {
        command: Command,
    },
    Invite {
        workspace_ids: Vec<String>,
        ttl_seconds: u32,
    },
    Join {
        ticket: String,
    },
    Sync,
    Foreground {
        foreground: bool,
    },
    Shutdown,
}

#[tokio::main(worker_threads = 2)]
async fn main() -> Result<()> {
    let (directory, name, mode) = arguments()?;
    let host = Host::open(directory, name, mode).await?;
    let mut output = tokio::io::stdout();
    let snapshot = host.snapshot().await?;
    emit(
        &mut output,
        json!({"event":"ready", "device_id":snapshot.data.device_id}),
    )
    .await?;
    let mut input = BufReader::new(tokio::io::stdin());
    let mut line = Vec::new();
    loop {
        line.clear();
        let count = (&mut input)
            .take(256 * 1024 + 1)
            .read_until(b'\n', &mut line)
            .await?;
        if count == 0 {
            break;
        }
        if line.len() > 256 * 1024 {
            emit(
                &mut output,
                json!({"id":null,"ok":false,"error":"request exceeds 256 KiB"}),
            )
            .await?;
            break;
        }
        let call: Call = match serde_json::from_slice(&line) {
            Ok(call) => call,
            Err(_) => {
                emit(
                    &mut output,
                    json!({"id":null,"ok":false,"error":"invalid request"}),
                )
                .await?;
                continue;
            }
        };
        if call.id.is_empty() || call.id.len() > 160 {
            emit(
                &mut output,
                json!({"id":null,"ok":false,"error":"invalid request identity"}),
            )
            .await?;
            continue;
        }
        let stopping = matches!(call.request, Method::Shutdown);
        let result: Result<Value> = async {
            Ok(match call.request {
                Method::Snapshot => serde_json::to_value(host.snapshot().await?)?,
                Method::Command { command } => serde_json::to_value(host.command(command).await?)?,
                Method::Invite {
                    workspace_ids,
                    ttl_seconds,
                } => serde_json::to_value(host.create_invite(workspace_ids, ttl_seconds).await?)?,
                Method::Join { ticket } => serde_json::to_value(host.join(ticket).await?)?,
                Method::Sync => serde_json::to_value(host.sync().await?)?,
                Method::Foreground { foreground } => {
                    host.set_foreground(foreground).await?;
                    serde_json::to_value(host.snapshot().await?)?
                }
                Method::Shutdown => {
                    host.shutdown().await?;
                    json!({"closed":true})
                }
            })
        }
        .await;
        let response = match result {
            Ok(value) => json!({"id":call.id,"ok":true,"result":value}),
            Err(error) => json!({"id":call.id,"ok":false,"error":error.to_string()}),
        };
        emit(&mut output, response).await?;
        if stopping {
            break;
        }
    }
    host.shutdown().await?;
    Ok(())
}

async fn emit(output: &mut tokio::io::Stdout, value: Value) -> Result<()> {
    output.write_all(&serde_json::to_vec(&value)?).await?;
    output.write_all(b"\n").await?;
    output.flush().await?;
    Ok(())
}

fn arguments() -> Result<(PathBuf, String, NetworkMode)> {
    let mut args = std::env::args().skip(1);
    let mut directory = None;
    let mut name = None;
    let mut mode = NetworkMode::Default;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--data-dir" => {
                directory = Some(PathBuf::from(
                    args.next().context("--data-dir needs a path")?,
                ))
            }
            "--name" => name = Some(args.next().context("--name needs a value")?),
            "--network" => {
                mode = match args.next().as_deref() {
                    Some("default") => NetworkMode::Default,
                    Some("loopback") => NetworkMode::Loopback,
                    _ => bail!("--network must be default or loopback"),
                }
            }
            _ => bail!(
                "usage: workspace-peer --data-dir PATH --name NAME [--network default|loopback]"
            ),
        }
    }
    let name = name.unwrap_or_else(|| "Device".to_owned());
    ensure!(
        !name.trim().is_empty() && name.len() <= 120,
        "device name must contain 1–120 bytes"
    );
    Ok((directory.context("--data-dir is required")?, name, mode))
}
