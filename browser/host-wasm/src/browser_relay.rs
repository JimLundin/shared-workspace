//! Browser URL compatibility without changing relay or peer authority.

use anyhow::Result;
use iroh::{EndpointAddr, RelayMap, RelayUrl, TransportAddr};

fn relay_url(relay: RelayUrl) -> Result<RelayUrl> {
    let mut url = (*relay).clone();
    if let Some(host) = url.domain().and_then(|domain| domain.strip_suffix('.')) {
        let host = host.to_owned();
        url.set_host(Some(&host))?;
    }
    Ok(url.into())
}

pub(crate) fn default_relays() -> Result<RelayMap> {
    let configs = iroh::defaults::prod::default_relay_map()
        .relays::<Vec<_>>()
        .into_iter()
        .map(|config| {
            let mut config = (*config).clone();
            config.url = relay_url(config.url)?;
            Ok(config)
        })
        .collect::<Result<Vec<_>>>()?;
    Ok(RelayMap::from_iter(configs))
}

pub(crate) fn peer_address(mut address: EndpointAddr) -> Result<EndpointAddr> {
    address.addrs = address
        .addrs
        .into_iter()
        .map(|locator| match locator {
            TransportAddr::Relay(url) => relay_url(url).map(TransportAddr::Relay),
            other => Ok(other),
        })
        .collect::<Result<_>>()?;
    Ok(address)
}

#[cfg(test)]
mod tests {
    use super::*;
    use iroh::SecretKey;

    #[test]
    fn browser_relay_hostname_changes_only_its_terminal_dns_dot() -> Result<()> {
        let original: RelayUrl =
            "https://relay.example.:8443/a.b/path.?q=keep.#fragment.".parse()?;
        let normalized = relay_url(original.clone())?;
        assert_eq!(normalized.domain(), Some("relay.example"));
        assert_eq!(normalized.scheme(), original.scheme());
        assert_eq!(normalized.port(), original.port());
        assert_eq!(normalized.path(), original.path());
        assert_eq!(normalized.query(), original.query());
        assert_eq!(normalized.fragment(), original.fragment());
        for input in [
            "https://relay.example/path.",
            "https://127.0.0.1:8443/path.",
            "https://[::1]:8443/path.",
        ] {
            let original: RelayUrl = input.parse()?;
            assert_eq!(relay_url(original.clone())?, original);
        }
        Ok(())
    }

    #[test]
    fn browser_uses_the_same_default_relays_with_compatible_hostnames() -> Result<()> {
        let original = iroh::defaults::prod::default_relay_map();
        let normalized = default_relays()?;
        assert_eq!(normalized.len(), original.len());
        for config in original.relays::<Vec<_>>() {
            let expected_url = relay_url(config.url.clone())?;
            let actual = normalized
                .get(&expected_url)
                .expect("every configured relay remains");
            assert!(!actual.url.domain().unwrap().ends_with('.'));
            let mut expected = (*config).clone();
            expected.url = expected_url;
            assert_eq!(*actual, expected);
        }
        Ok(())
    }

    #[test]
    fn legacy_peer_relay_hints_preserve_identity_and_ip_locators() -> Result<()> {
        let id = SecretKey::from_bytes(&[19; 32]).public();
        let ip = "127.0.0.1:23456".parse()?;
        let address = EndpointAddr::new(id)
            .with_relay_url("https://relay.example./".parse()?)
            .with_ip_addr(ip);
        let normalized = peer_address(address)?;
        assert_eq!(normalized.id, id);
        assert!(normalized.addrs.contains(&TransportAddr::Ip(ip)));
        assert!(
            normalized
                .addrs
                .contains(&TransportAddr::Relay("https://relay.example/".parse()?))
        );
        assert_eq!(normalized.addrs.len(), 2);
        Ok(())
    }
}
