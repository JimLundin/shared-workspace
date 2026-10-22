# Windows UI Automation, scoped to one window. Control identifiers come from
# RuntimeId rather than positions or names that may refer to multiple elements.
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr][long]$request.handle)
if ($null -eq $root) { throw 'The window is no longer available. List windows again.' }

function Pattern($element, $identifier) {
    $found = $null
    if ($element.TryGetCurrentPattern($identifier, [ref]$found)) { return $found }
    return $null
}

function Clip([string]$text, [int]$limit = 500) {
    if ($text.Length -gt $limit) { return $text.Substring(0, $limit) + '...' }
    return $text
}

function Describe($element, $depth) {
    $current = $element.Current
    $actions = [Collections.Generic.List[string]]::new()
    $value = $null
    foreach ($pattern in $element.GetSupportedPatterns()) {
        switch ($pattern.ProgrammaticName) {
            'InvokePatternIdentifiers.Pattern' { $actions.Add('invoke') }
            'ValuePatternIdentifiers.Pattern' {
                if (-not $current.IsPassword) {
                    $details = (Pattern $element ([System.Windows.Automation.ValuePattern]::Pattern)).Current
                    $value = Clip $details.Value
                    if (-not $details.IsReadOnly) { $actions.Add('set_value') }
                }
            }
            'RangeValuePatternIdentifiers.Pattern' {
                $details = (Pattern $element ([System.Windows.Automation.RangeValuePattern]::Pattern)).Current
                $value = @{ value = $details.Value; minimum = $details.Minimum; maximum = $details.Maximum }
                if (-not $details.IsReadOnly) { $actions.Add('set_range') }
            }
            'TogglePatternIdentifiers.Pattern' {
                $value = (Pattern $element ([System.Windows.Automation.TogglePattern]::Pattern)).Current.ToggleState.ToString()
                $actions.Add('toggle')
            }
            'SelectionItemPatternIdentifiers.Pattern' {
                $value = (Pattern $element ([System.Windows.Automation.SelectionItemPattern]::Pattern)).Current.IsSelected
                $actions.Add('select')
            }
            'ExpandCollapsePatternIdentifiers.Pattern' {
                $value = (Pattern $element ([System.Windows.Automation.ExpandCollapsePattern]::Pattern)).Current.ExpandCollapseState.ToString()
                $actions.Add('expand'); $actions.Add('collapse')
            }
            'ScrollItemPatternIdentifiers.Pattern' { $actions.Add('scroll_into_view') }
            'ScrollPatternIdentifiers.Pattern' {
                $details = (Pattern $element ([System.Windows.Automation.ScrollPattern]::Pattern)).Current
                $value = @{ horizontal = $details.HorizontalScrollPercent; vertical = $details.VerticalScrollPercent }
                $actions.Add('scroll')
            }
        }
    }
    if ($current.IsKeyboardFocusable) { $actions.Add('focus') }
    if ($current.IsPassword) { $value = $null } else { $actions.Add('read') }
    $name = Clip $current.Name
    $bounds = $current.BoundingRectangle
    return @{
        id = ($element.GetRuntimeId() -join '.'); depth = $depth
        name = $name; role = $current.ControlType.ProgrammaticName.Replace('ControlType.', '')
        automation_id = (Clip $current.AutomationId); class_name = (Clip $current.ClassName)
        enabled = $current.IsEnabled; offscreen = $current.IsOffscreen; password = $current.IsPassword
        bounds = if ($bounds.IsEmpty) { $null } else { @{
            x = [int]$bounds.X; y = [int]$bounds.Y; width = [int]$bounds.Width; height = [int]$bounds.Height
        } }
        actions = @($actions.ToArray()); value = $value
    }
}

if ($request.operation -eq 'inspect') {
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $stack = [Collections.Generic.Stack[object]]::new()
    $stack.Push(@{ element = $root; depth = 0 })
    $rows = [Collections.Generic.List[object]]::new()
    $visited = 0
    $matched = 0
    $characters = 0
    $more = $false
    while ($stack.Count -and $visited -lt 4000) {
        $entry = $stack.Pop()
        $visited++
        try {
            $row = Describe $entry.element $entry.depth
            if (-not $request.query -or ($row.name + ' ' + $row.automation_id).IndexOf(
                    [string]$request.query, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                if ($matched -ge $request.offset) {
                    $length = ($row | ConvertTo-Json -Depth 8 -Compress).Length
                    if ($rows.Count -ge $request.limit -or ($rows.Count -gt 0 -and $characters + $length -gt 15000)) {
                        $more = $true; break
                    }
                    $rows.Add($row)
                    $characters += $length
                }
                $matched++
            }
            if ($entry.depth -lt $request.depth) {
                $children = [Collections.Generic.List[object]]::new()
                $child = $walker.GetFirstChild($entry.element)
                while ($null -ne $child -and $children.Count -lt 4000) {
                    $children.Add($child)
                    $child = $walker.GetNextSibling($child)
                }
                for ($i = $children.Count - 1; $i -ge 0; $i--) {
                    $stack.Push(@{ element = $children[$i]; depth = $entry.depth + 1 })
                }
            }
        } catch [System.Windows.Automation.ElementNotAvailableException] {
            # A changing window may remove a control while it is being read.
        }
    }
    @{
        handle = [long]$request.handle; controls = @($rows.ToArray()); visited = $visited
        next_offset = if ($more) { $request.offset + $rows.Count } else { $null }
        truncated = ($more -or $stack.Count -gt 0)
        coordinates = 'physical screen pixels'
    } | ConvertTo-Json -Depth 10 -Compress
    return
}

if (($root.GetRuntimeId() -join '.') -eq $request.id) {
    $element = $root
} else {
    try { $runtimeId = [int[]]([string]$request.id).Split('.') } catch { throw 'Invalid control id. Inspect the window again.' }
    $condition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::RuntimeIdProperty, $runtimeId)
    $element = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condition)
}
if ($null -eq $element) { throw 'The control has changed or disappeared. Inspect the window again.' }
if ($request.action -ne 'read' -and -not $element.Current.IsEnabled) { throw 'The control is disabled.' }

$supported = (Describe $element 0).actions
if ($request.action -notin $supported) { throw ('Unsupported action. Available actions: ' + ($supported -join ', ')) }
switch ($request.action) {
    'read' {
        $text = Pattern $element ([System.Windows.Automation.TextPattern]::Pattern)
        $selection = [Collections.Generic.List[string]]::new()
        $truncated = $false
        if ($null -ne $text) {
            $content = $text.DocumentRange.GetText(12001)
            $remaining = 4000
            foreach ($range in $text.GetSelection()) {
                if ($remaining -le 0 -or $selection.Count -ge 8) { $truncated = $true; break }
                $selected = $range.GetText($remaining + 1)
                if ($selected.Length -gt $remaining) { $selected = $selected.Substring(0, $remaining); $truncated = $true }
                $selection.Add($selected)
                $remaining -= $selected.Length
            }
        } else {
            $content = $element.Current.Name
            $value = Pattern $element ([System.Windows.Automation.ValuePattern]::Pattern)
            if ($null -ne $value) { $content = $value.Current.Value }
        }
        if ($content.Length -gt 12000) { $content = $content.Substring(0, 12000); $truncated = $true }
        $result = @{ control = (Describe $element 0); text = $content; selection = @($selection.ToArray()); truncated = $truncated }
        # Bound the serialized size as well: quotes and control characters expand
        # in JSON. Always return valid JSON instead of the command runner's cut.
        do {
            $encoded = $result | ConvertTo-Json -Depth 10 -Compress
            if ($encoded.Length -le 18000) { break }
            $result.text = $result.text.Substring(0, [int]($result.text.Length / 2))
            $result.selection = @($result.selection | ForEach-Object { $_.Substring(0, [int]($_.Length / 2)) })
            $result.truncated = $true
        } while ($true)
        $encoded
        return
    }
    'invoke' { (Pattern $element ([System.Windows.Automation.InvokePattern]::Pattern)).Invoke() }
    'set_value' { (Pattern $element ([System.Windows.Automation.ValuePattern]::Pattern)).SetValue([string]$request.value) }
    'set_range' {
        $value = [double]::Parse([string]$request.value, [Globalization.CultureInfo]::InvariantCulture)
        (Pattern $element ([System.Windows.Automation.RangeValuePattern]::Pattern)).SetValue($value)
    }
    'toggle' { (Pattern $element ([System.Windows.Automation.TogglePattern]::Pattern)).Toggle() }
    'select' { (Pattern $element ([System.Windows.Automation.SelectionItemPattern]::Pattern)).Select() }
    'expand' { (Pattern $element ([System.Windows.Automation.ExpandCollapsePattern]::Pattern)).Expand() }
    'collapse' { (Pattern $element ([System.Windows.Automation.ExpandCollapsePattern]::Pattern)).Collapse() }
    'scroll_into_view' { (Pattern $element ([System.Windows.Automation.ScrollItemPattern]::Pattern)).ScrollIntoView() }
    'scroll' {
        (Pattern $element ([System.Windows.Automation.ScrollPattern]::Pattern)).SetScrollPercent(
            [double]$request.horizontal, [double]$request.vertical)
    }
    'focus' { $element.SetFocus() }
}
$after = $null
try { $after = Describe $element 0 } catch [System.Windows.Automation.ElementNotAvailableException] { }
@{ action = $request.action; control = $after; note = 'Inspect the updated window to verify the result.' } | ConvertTo-Json -Depth 10 -Compress
