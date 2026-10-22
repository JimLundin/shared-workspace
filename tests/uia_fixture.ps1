# A separate, non-activating test window. Tests operate only its accessibility
# controls; they never send keyboard or mouse input to the user's desktop.
Add-Type -AssemblyName PresentationFramework
$window = [Windows.Markup.XamlReader]::Parse(@'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Toolkit accessibility fixture" Width="420" Height="560"
        Left="40" Top="40" WindowStartupLocation="Manual" WindowStyle="ToolWindow"
        ShowActivated="False" ShowInTaskbar="False">
  <StackPanel Margin="12">
    <TextBox x:Name="editor" AutomationProperties.AutomationId="fixture_editor" Text="Initial text" />
    <Button x:Name="button" AutomationProperties.AutomationId="fixture_button" Content="Apply fixture" />
    <Label x:Name="status" AutomationProperties.AutomationId="fixture_status" Content="Pressed 0" />
    <CheckBox x:Name="checkbox" AutomationProperties.AutomationId="fixture_checkbox" Content="Fixture option" />
    <Slider x:Name="slider" AutomationProperties.AutomationId="fixture_slider" Minimum="0" Maximum="100" Value="20" />
    <ListBox x:Name="list" AutomationProperties.AutomationId="fixture_list" Height="80">
      <ListBoxItem AutomationProperties.AutomationId="fixture_alpha">Alpha</ListBoxItem>
      <ListBoxItem AutomationProperties.AutomationId="fixture_beta">Beta</ListBoxItem>
    </ListBox>
    <ComboBox x:Name="choices" AutomationProperties.AutomationId="fixture_choices">
      <ComboBoxItem>One</ComboBoxItem><ComboBoxItem>Two</ComboBoxItem>
    </ComboBox>
    <PasswordBox AutomationProperties.AutomationId="fixture_password" Password="fixture-only-secret" />
    <Button AutomationProperties.AutomationId="fixture_disabled" Content="Disabled" IsEnabled="False" />
  </StackPanel>
</Window>
'@)
$script:clicks = 0
$button = $window.FindName('button')
$status = $window.FindName('status')
$button.Add_Click({ $script:clicks++; $status.Content = 'Pressed ' + $script:clicks })
$window.Show()
$handle = [Windows.Interop.WindowInteropHelper]::new($window).Handle.ToInt64()
$info = @{ handle = $handle } | ConvertTo-Json -Compress
[IO.File]::WriteAllText((Join-Path $fixtureRoot 'window.json'), $info)
[IO.File]::WriteAllText((Join-Path $fixtureRoot 'ready'), '')
$started = [DateTime]::UtcNow
$timer = [Windows.Threading.DispatcherTimer]::new()
$timer.Interval = [TimeSpan]::FromMilliseconds(100)
$timer.Add_Tick({
    if ((Test-Path -LiteralPath (Join-Path $fixtureRoot 'stop')) -or ([DateTime]::UtcNow - $started).TotalSeconds -gt 90) {
        $timer.Stop()
        $window.Close()
        [Windows.Threading.Dispatcher]::ExitAllFrames()
    }
})
$timer.Start()
try { [Windows.Threading.Dispatcher]::Run() } finally { $timer.Stop(); $window.Close() }
