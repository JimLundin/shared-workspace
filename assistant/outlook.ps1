# Attach only. This bridge never starts Outlook, opens an inspector, or sends mail.
$ErrorActionPreference = 'Stop'
$keyName = 'ToolkitWorkflowDraftKey'
$fingerprintName = 'ToolkitWorkflowDraftFingerprint'
$application = $null
$session = $null
$folder = $null
$item = $null
$mutex = $null
$ownsMutex = $false

function Write-Result($value) {
    $json = ConvertTo-Json -InputObject $value -Depth 12 -Compress
    [IO.File]::WriteAllText($request.response_path, $json, [Text.UTF8Encoding]::new($false))
}

function Release-Com($value) {
    if ($null -ne $value -and [Runtime.InteropServices.Marshal]::IsComObject($value)) {
        try { $null = [Runtime.InteropServices.Marshal]::ReleaseComObject($value) } catch { }
    }
}

function Find-Draft($draftFolder, [string]$key) {
    $definitions = $null
    $definition = $null
    $items = $null
    $matching = $null
    try {
        $definitions = $draftFolder.UserDefinedProperties
        $definition = $definitions.Find($keyName)
        if ($null -eq $definition) { return @{ kind = 'missing' } }
        $items = $draftFolder.Items
        # key is a checked SHA-256 string, never user content or an email subject.
        $matching = $items.Restrict("[$keyName] = '$key'")
        if ($matching.Count -gt 1) {
            return @{ kind = 'conflict'; reason = 'Multiple drafts have this workflow identity. None was changed.' }
        }
        if ($matching.Count -eq 0) { return @{ kind = 'missing' } }
        return @{ kind = 'found'; item = $matching.Item(1) }
    } finally {
        Release-Com $matching
        Release-Com $items
        Release-Com $definition
        Release-Com $definitions
    }
}

function Recipient-Smtp($recipient) {
    $entry = $null
    $accessor = $null
    $exchange = $null
    try {
        if (-not $recipient.Resolved) { throw 'An Outlook recipient is unresolved.' }
        $entry = $recipient.AddressEntry
        if ($entry.Type -eq 'SMTP') { return [string]$entry.Address }
        $accessor = $entry.PropertyAccessor
        try {
            $smtp = [string]$accessor.GetProperty('http://schemas.microsoft.com/mapi/proptag/0x39FE001F')
            if ($smtp) { return $smtp }
        } catch { }
        if ($entry.Type -eq 'EX') {
            $exchange = $entry.GetExchangeUser()
            if ($null -eq $exchange) { $exchange = $entry.GetExchangeDistributionList() }
            if ($null -ne $exchange -and $exchange.PrimarySmtpAddress) { return [string]$exchange.PrimarySmtpAddress }
        }
        throw 'An Outlook recipient has no verified SMTP address.'
    } finally {
        Release-Com $exchange
        Release-Com $accessor
        Release-Com $entry
    }
}

function Read-Draft($draft, $expectedFolder) {
    $parent = $null
    $properties = $null
    $keyProperty = $null
    $fingerprintProperty = $null
    $recipients = $null
    try {
        if ($draft.Class -ne 43 -or $draft.Sent -or $draft.Submitted -or -not $draft.Saved) {
            return @{ kind = 'conflict'; reason = 'The identified item is not an unchanged, saved Outlook draft.' }
        }
        $parent = $draft.Parent
        if ($parent.EntryID -ne $expectedFolder.entry_id -or $parent.StoreID -ne $expectedFolder.store_id) {
            return @{ kind = 'conflict'; reason = 'The draft moved from its original mailbox or folder.' }
        }
        $properties = $draft.UserProperties
        $keyProperty = $properties.Find($keyName, $true)
        $fingerprintProperty = $properties.Find($fingerprintName, $true)
        if ($null -eq $keyProperty -or $null -eq $fingerprintProperty -or $keyProperty.Value -ne $request.key) {
            return @{ kind = 'conflict'; reason = 'The saved draft has a different workflow identity.' }
        }
        $to = [Collections.Generic.List[string]]::new()
        $cc = [Collections.Generic.List[string]]::new()
        $bcc = [Collections.Generic.List[string]]::new()
        $recipients = $draft.Recipients
        if ($recipients.Count -gt 200) { throw 'The saved draft has too many recipients to verify.' }
        for ($i = 1; $i -le $recipients.Count; $i++) {
            $recipient = $null
            try {
                $recipient = $recipients.Item($i)
                $address = Recipient-Smtp $recipient
                switch ($recipient.Type) {
                    1 { $to.Add($address) }
                    2 { $cc.Add($address) }
                    3 { $bcc.Add($address) }
                    default { throw 'The draft has an unsupported recipient role.' }
                }
            } finally { Release-Com $recipient }
        }
        return @{
            kind = 'draft'; entry_id = [string]$draft.EntryID
            folder = @{ entry_id = [string]$parent.EntryID; store_id = [string]$parent.StoreID }
            key = [string]$keyProperty.Value; fingerprint = [string]$fingerprintProperty.Value
            saved = [bool]$draft.Saved; sent = [bool]$draft.Sent; submitted = [bool]$draft.Submitted
            content = @{
                subject = [string]$draft.Subject; body = [string]$draft.Body
                to = @($to.ToArray()); cc = @($cc.ToArray()); bcc = @($bcc.ToArray())
            }
        }
    } finally {
        Release-Com $recipients
        Release-Com $fingerprintProperty
        Release-Com $keyProperty
        Release-Com $properties
        Release-Com $parent
    }
}

try {
    try { $application = [Runtime.InteropServices.Marshal]::GetActiveObject('Outlook.Application') }
    catch {
        Write-Result @{ kind = 'unavailable'; reason = 'No running classic Outlook automation session is available. New Outlook does not expose this local draft interface.' }
        return
    }
    $session = $application.Session
    if ($request.operation -eq 'status') {
        try {
            $folder = $session.GetDefaultFolder(16) # olFolderDrafts
            Write-Result @{ kind = 'available'; folder = @{ entry_id = [string]$folder.EntryID; store_id = [string]$folder.StoreID } }
        } catch {
            Write-Result @{ kind = 'unavailable'; reason = 'The existing Outlook session has no accessible default Drafts folder.' }
        }
        return
    }
    if ($request.key -notmatch '^[0-9a-f]{64}$' -or $request.operation -notin @('ensure', 'find', 'read')) {
        throw 'Invalid draft operation.'
    }
    # Both folder and store were selected and durably recorded before Save.
    $folder = $session.GetFolderFromID($request.folder.entry_id, $request.folder.store_id)
    $mutex = [Threading.Mutex]::new($false, ('Local\ToolkitOutlookDraft-' + $request.key))
    try { $ownsMutex = $mutex.WaitOne(5000) }
    catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) { throw 'Another process is still saving this draft.' }
    if ($request.operation -eq 'read') {
        try { $item = $session.GetItemFromID($request.entry_id, $request.folder.store_id) }
        catch { Write-Result @{ kind = 'missing' }; return }
    } else {
        $found = Find-Draft $folder $request.key
        if ($found.kind -eq 'conflict') { Write-Result $found; return }
        if ($found.kind -eq 'found') { $item = $found.item }
        elseif ($request.operation -eq 'find') { Write-Result @{ kind = 'missing' }; return }
        else {
            if ($request.fingerprint -notmatch '^[0-9a-f]{64}$') { throw 'Invalid draft fingerprint.' }
            $definitions = $null
            $definition = $null
            $properties = $null
            $keyProperty = $null
            $fingerprintProperty = $null
            $recipients = $null
            $items = $null
            try {
                $definitions = $folder.UserDefinedProperties
                $definition = $definitions.Find($keyName)
                if ($null -eq $definition) { $definition = $definitions.Add($keyName, 1) }
                $items = $folder.Items
                $item = $items.Add('IPM.Note')
                $properties = $item.UserProperties
                $keyProperty = $properties.Add($keyName, 1, $true)
                $keyProperty.Value = $request.key
                $fingerprintProperty = $properties.Add($fingerprintName, 1, $true)
                $fingerprintProperty.Value = $request.fingerprint
                $item.BodyFormat = 1 # olFormatPlain; no signature or editor is opened.
                $item.Subject = $request.content.subject
                $item.Body = $request.content.body
                $recipients = $item.Recipients
                foreach ($role in @(@{ name = 'to'; type = 1 }, @{ name = 'cc'; type = 2 }, @{ name = 'bcc'; type = 3 })) {
                    foreach ($address in $request.content.($role.name)) {
                        $recipient = $null
                        try {
                            $recipient = $recipients.Add([string]$address)
                            $recipient.Type = $role.type
                        } finally { Release-Com $recipient }
                    }
                }
                if ($recipients.Count -gt 0 -and -not $recipients.ResolveAll()) { throw 'A requested SMTP recipient did not resolve.' }
                $item.Save()
                $entryID = [string]$item.EntryID
                Release-Com $item
                $item = $null
                $item = $session.GetItemFromID($entryID, $request.folder.store_id)
            } finally {
                Release-Com $recipients
                Release-Com $fingerprintProperty
                Release-Com $keyProperty
                Release-Com $properties
                Release-Com $items
                Release-Com $definition
                Release-Com $definitions
            }
        }
    }
    Write-Result (Read-Draft $item $request.folder)
} catch {
    # Never include native diagnostics: they can contain profile or message data.
    Write-Result @{ kind = 'uncertain'; reason = 'Outlook did not return a complete verified draft. Retry only by its original workflow identity and mailbox.' }
} finally {
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    if ($null -ne $mutex) { $mutex.Dispose() }
    Release-Com $item
    Release-Com $folder
    Release-Com $session
    Release-Com $application
}
