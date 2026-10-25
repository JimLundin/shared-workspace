//! Device key and process ownership stay local and never enter peer exports.
use anyhow::{Context, Result, ensure};
use iroh::SecretKey;
use std::{
    fs::{self, File, OpenOptions},
    io::Write,
    path::Path,
};

pub(crate) fn open(directory: &Path) -> Result<(SecretKey, File)> {
    fs::create_dir_all(directory)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(directory, fs::Permissions::from_mode(0o700))?;
    }
    let mut options = OpenOptions::new();
    options.read(true).write(true).create(true).truncate(false);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let owner = options.open(directory.join("owner.lock"))?;
    owner
        .try_lock()
        .context("another host owns this data directory")?;
    let key_path = directory.join("device.key");
    let key = if key_path.exists() {
        let bytes = fs::read(&key_path)?;
        ensure!(
            bytes.len() == 32,
            "stored device identity is invalid; refusing to rotate it"
        );
        SecretKey::from_bytes(bytes.as_slice().try_into().unwrap())
    } else {
        let key = SecretKey::generate();
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&key_path)?;
        file.write_all(&key.to_bytes())?;
        file.sync_all()?;
        #[cfg(unix)]
        {
            File::open(directory)?.sync_all()?;
        }
        key
    };
    Ok((key, owner))
}
