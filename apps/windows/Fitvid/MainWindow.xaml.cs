using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Storage.Pickers;
using WinRT.Interop;

namespace Fitvid;

public sealed partial class MainWindow : Window
{
    private readonly AppState _state = new();
    private readonly FitvidCli _cli = new();

    public MainWindow()
    {
        InitializeComponent();
        Title = "fitvid";
        ShowWizard();
    }

    private void ShowWizard()
    {
        WizardPanel.Visibility = Visibility.Visible;
        MainPanel.Visibility = Visibility.Collapsed;
        UpdateWizardCopy();
    }

    private void ShowMain()
    {
        WizardPanel.Visibility = Visibility.Collapsed;
        MainPanel.Visibility = Visibility.Visible;
        RefreshFieldList();
        RefreshMediaDock();
    }

    private void UpdateWizardCopy()
    {
        WizardTitle.Text = _state.Step switch
        {
            WizardStep.Fit => "Select activity",
            WizardStep.SyncFit => "Sync FIT / GPS watch",
            WizardStep.Videos => "Add video clips",
            WizardStep.SyncCameras => "Sync camera clocks",
            WizardStep.Audio => "Add standalone audio?",
            WizardStep.SyncAudio => "Sync audio recorder clocks",
            _ => ""
        };
        WizardSubtitle.Text = _state.Step switch
        {
            WizardStep.Fit => "Pick the .fit file from your GPS watch or bike computer.",
            WizardStep.SyncFit => "Confirm the sync moment and what the watch showed. Optional: enter true wall-clock (phone) time.",
            WizardStep.Videos => "Select one or more videos that cover this activity.",
            WizardStep.SyncCameras => "Videos are grouped by recording device. Enter what each camera showed at the sync moment.",
            WizardStep.Audio => "Optional recorder audio. Skip if you will use camera audio.",
            WizardStep.SyncAudio => "Enter what each audio recorder showed at the sync moment.",
            _ => ""
        };
        SkipAudioButton.Visibility = _state.Step == WizardStep.Audio ? Visibility.Visible : Visibility.Collapsed;
        PrimaryWizardButton.Content = _state.Step switch
        {
            WizardStep.Fit => "Choose FIT file…",
            WizardStep.Videos => "Choose videos…",
            WizardStep.Audio => "Choose audio…",
            _ => "Continue"
        };
        FitSyncPanel.Visibility = _state.Step == WizardStep.SyncFit ? Visibility.Visible : Visibility.Collapsed;
        DeviceSyncPanel.Visibility = _state.Step is WizardStep.SyncCameras or WizardStep.SyncAudio
            ? Visibility.Visible : Visibility.Collapsed;
        if (_state.Step == WizardStep.SyncFit)
        {
            FitLabelBox.Text = _state.FitLabel;
            FitReferenceBox.Text = _state.FitReferenceIso;
            WatchClockBox.Text = _state.WatchClockIso;
            WallClockBox.Text = _state.WallClockIso;
        }
        if (_state.Step == WizardStep.SyncCameras)
            RebuildDeviceSyncHost(_state.CameraGroups, "Cameras");
        if (_state.Step == WizardStep.SyncAudio)
            RebuildDeviceSyncHost(_state.AudioGroups, "Audio recorders");
    }

    private void RebuildDeviceSyncHost(List<DeviceGroup> groups, string title)
    {
        DeviceSyncTitle.Text = title;
        DeviceSyncHost.Children.Clear();
        foreach (var g in groups)
        {
            var card = new StackPanel { Spacing = 6 };
            var labelBox = new TextBox { Header = "Label", Text = g.Label, Tag = g };
            labelBox.TextChanged += (s, _) =>
            {
                if (s is TextBox tb && tb.Tag is DeviceGroup d) d.Label = tb.Text ?? d.Label;
            };
            var files = new TextBlock
            {
                Text = string.Join(", ", g.Clips.Select(c => Path.GetFileName(c.Path))),
                Opacity = 0.6,
                TextWrapping = TextWrapping.Wrap,
                FontSize = 12
            };
            var clockBox = new TextBox
            {
                Header = "Device clock at sync moment (ISO)",
                Text = g.DeviceClockIso,
                Tag = g
            };
            clockBox.TextChanged += (s, _) =>
            {
                if (s is TextBox tb && tb.Tag is DeviceGroup d) d.DeviceClockIso = tb.Text ?? "";
            };
            card.Children.Add(labelBox);
            card.Children.Add(files);
            card.Children.Add(clockBox);
            DeviceSyncHost.Children.Add(card);
        }
    }

    private void ReadFitSyncFromUi()
    {
        _state.FitLabel = FitLabelBox.Text ?? _state.FitLabel;
        _state.FitReferenceIso = FitReferenceBox.Text ?? _state.FitReferenceIso;
        _state.WatchClockIso = WatchClockBox.Text ?? _state.WatchClockIso;
        _state.WallClockIso = WallClockBox.Text ?? "";
    }

    private async void PrimaryWizardButton_Click(object sender, RoutedEventArgs e)
    {
        switch (_state.Step)
        {
            case WizardStep.Fit:
                await PickFitAsync();
                break;
            case WizardStep.SyncFit:
                ReadFitSyncFromUi();
                _state.Step = WizardStep.Videos;
                UpdateWizardCopy();
                break;
            case WizardStep.Videos:
                await PickVideosAsync(replace: true);
                if (_state.AllVideoClips.Count > 0)
                {
                    await ProbeCamerasAsync();
                    _state.Step = WizardStep.SyncCameras;
                    UpdateWizardCopy();
                }
                break;
            case WizardStep.SyncCameras:
                LocalTimeSync.ApplyOffsets(_state, _state.CameraGroups);
                _state.Step = WizardStep.Audio;
                UpdateWizardCopy();
                break;
            case WizardStep.Audio:
                await PickAudioAsync();
                if (_state.AllAudioClips.Count > 0)
                {
                    await ProbeAudioAsync();
                    _state.Step = WizardStep.SyncAudio;
                    UpdateWizardCopy();
                }
                else
                {
                    _state.Step = WizardStep.Ready;
                    ShowMain();
                }
                break;
            case WizardStep.SyncAudio:
                LocalTimeSync.ApplyOffsets(_state, _state.AudioGroups);
                _state.Step = WizardStep.Ready;
                ShowMain();
                break;
        }
    }

    private void SkipAudioButton_Click(object sender, RoutedEventArgs e)
    {
        _state.AudioGroups.Clear();
        _state.Step = WizardStep.Ready;
        ShowMain();
    }

    private async Task PickFitAsync()
    {
        var picker = new FileOpenPicker();
        InitializePicker(picker);
        picker.FileTypeFilter.Add(".fit");
        var file = await picker.PickSingleFileAsync();
        if (file is null) return;
        try
        {
            StatusText.Text = "Inspecting…";
            var payload = await Task.Run(() => _cli.Inspect(file.Path));
            _state.FitPath = file.Path;
            _state.Inspect = payload;
            _state.FitLabel = payload.FitDevice?.Label ?? "FIT device";
            _state.FitReferenceIso = payload.SessionStart ?? "";
            _state.WatchClockIso = payload.SessionStart ?? "";
            _state.SelectedFields = payload.Fields
                .Where(f => f.Name is "speed" or "heart_rate" or "grade")
                .Select(f => f.Name)
                .ToHashSet();
            _state.IncludeMap = payload.HasGps;
            _state.ThresholdField = payload.Fields.FirstOrDefault(f => f.Name == "speed")?.Name
                ?? payload.Fields.FirstOrDefault()?.Name;
            _state.ThresholdValue = payload.Fields.FirstOrDefault(f => f.Name == _state.ThresholdField)?.Max ?? 0;
            AppendLog($"Inspected {Path.GetFileName(file.Path)}: {payload.Fields.Count} fields (local TZ)");
            _state.Step = WizardStep.SyncFit;
            UpdateWizardCopy();
            StatusText.Text = "";
        }
        catch (Exception ex)
        {
            StatusText.Text = ex.Message;
            AppendLog("Inspect failed: " + ex.Message);
        }
    }

    private async Task PickVideosAsync(bool replace)
    {
        var picker = new FileOpenPicker();
        InitializePicker(picker);
        picker.FileTypeFilter.Add(".mp4");
        picker.FileTypeFilter.Add(".mov");
        picker.FileTypeFilter.Add(".mkv");
        var files = await picker.PickMultipleFilesAsync();
        if (files is null || files.Count == 0) return;
        var paths = files.Select(f => f.Path).ToList();
        if (replace)
        {
            _state.CameraGroups = new List<DeviceGroup>
            {
                new()
                {
                    Id = "camera-pending",
                    Label = "Camera",
                    DeviceClockIso = _state.FitReferenceIso,
                    Clips = paths.Select(p => MediaClip.Placeholder(p, _state.FitReferenceIso)).ToList()
                }
            };
        }
        else
        {
            var existing = _state.AllVideoClips.Select(c => c.Path).ToHashSet(StringComparer.OrdinalIgnoreCase);
            var added = paths.Where(p => !existing.Contains(p)).ToList();
            if (added.Count == 0) return;
            var all = _state.AllVideoClips.Select(c => c.Path).Concat(added).ToList();
            _state.CameraGroups = new List<DeviceGroup>
            {
                new()
                {
                    Id = "camera-pending",
                    Label = "Camera",
                    DeviceClockIso = _state.FitReferenceIso,
                    Clips = all.Select(p => MediaClip.Placeholder(p, _state.FitReferenceIso)).ToList()
                }
            };
        }
        RefreshMediaDock();
    }

    private async Task ProbeCamerasAsync(bool applyOffsets = false)
    {
        StatusText.Text = "Probing cameras…";
        var paths = _state.AllVideoClips.Select(c => c.Path).ToList();
        try
        {
            var devices = await Task.Run(() => _cli.ProbeMedia(paths));
            _state.CameraGroups = DeviceGroup.FromProbe(devices, _state.FitReferenceIso, "video");
        }
        catch (Exception ex)
        {
            AppendLog("probe-media failed, using one camera group: " + ex.Message);
            _state.CameraGroups = new List<DeviceGroup>
            {
                new()
                {
                    Id = "camera-1",
                    Label = "Camera",
                    DeviceClockIso = _state.FitReferenceIso,
                    Clips = paths.Select(p => MediaClip.Placeholder(p, _state.FitReferenceIso)).ToList()
                }
            };
        }
        if (applyOffsets) LocalTimeSync.ApplyOffsets(_state, _state.CameraGroups);
        StatusText.Text = "";
        RefreshMediaDock();
    }

    private async Task PickAudioAsync()
    {
        var picker = new FileOpenPicker();
        InitializePicker(picker);
        picker.FileTypeFilter.Add(".wav");
        picker.FileTypeFilter.Add(".m4a");
        picker.FileTypeFilter.Add(".mp3");
        var files = await picker.PickMultipleFilesAsync();
        if (files is null || files.Count == 0) return;
        var existing = _state.AllAudioClips.Select(c => c.Path).ToHashSet(StringComparer.OrdinalIgnoreCase);
        foreach (var f in files)
        {
            if (existing.Contains(f.Path)) continue;
            if (_state.AudioGroups.Count == 0)
            {
                _state.AudioGroups.Add(new DeviceGroup
                {
                    Id = "recorder-pending",
                    Label = "Audio recorder",
                    DeviceClockIso = _state.FitReferenceIso
                });
            }
            _state.AudioGroups[0].Clips.Add(MediaClip.Placeholder(f.Path, _state.FitReferenceIso));
        }
        RefreshMediaDock();
    }

    private async Task ProbeAudioAsync(bool applyOffsets = false)
    {
        StatusText.Text = "Probing audio recorders…";
        var paths = _state.AllAudioClips.Select(c => c.Path).ToList();
        try
        {
            var devices = await Task.Run(() => _cli.ProbeMedia(paths));
            _state.AudioGroups = DeviceGroup.FromProbe(devices, _state.FitReferenceIso, "audio", idSuffix: "-audio");
        }
        catch (Exception ex)
        {
            AppendLog("probe-media failed, using one recorder group: " + ex.Message);
            _state.AudioGroups = new List<DeviceGroup>
            {
                new()
                {
                    Id = "recorder-1",
                    Label = "Audio recorder",
                    DeviceClockIso = _state.FitReferenceIso,
                    Clips = paths.Select(p => MediaClip.Placeholder(p, _state.FitReferenceIso)).ToList()
                }
            };
        }
        if (applyOffsets) LocalTimeSync.ApplyOffsets(_state, _state.AudioGroups);
        StatusText.Text = "";
        RefreshMediaDock();
    }

    private void InitializePicker(FileOpenPicker picker)
    {
        var hwnd = WindowNative.GetWindowHandle(this);
        InitializeWithWindow.Initialize(picker, hwnd);
    }

    private void RefreshFieldList()
    {
        FieldList.Items.Clear();
        if (_state.Inspect is null) return;
        foreach (var group in _state.Inspect.Fields.GroupBy(f => f.Category))
        {
            FieldList.Items.Add(new ListViewHeaderItem { Content = group.Key });
            foreach (var field in group)
            {
                var toggle = new ToggleSwitch
                {
                    Header = $"{field.Label}  ({field.Unit} {field.Min:G4}–{field.Max:G4})",
                    IsOn = _state.SelectedFields.Contains(field.Name),
                    Tag = field.Name
                };
                toggle.Toggled += (s, _) =>
                {
                    var name = (string)((ToggleSwitch)s!).Tag;
                    if (((ToggleSwitch)s).IsOn) _state.SelectedFields.Add(name);
                    else _state.SelectedFields.Remove(name);
                };
                FieldList.Items.Add(toggle);
            }
        }
        MapToggle.IsOn = _state.IncludeMap;
        MapToggle.Visibility = _state.Inspect.HasGps ? Visibility.Visible : Visibility.Collapsed;
    }

    private void RefreshMediaDock()
    {
        VideoSourceHost.Children.Clear();
        foreach (var g in _state.CameraGroups)
            VideoSourceHost.Children.Add(BuildSourceCard(g, isVideo: true));
        if (_state.CameraGroups.Count == 0)
            VideoSourceHost.Children.Add(new TextBlock { Text = "No videos", Opacity = 0.5 });

        AudioSourceHost.Children.Clear();
        foreach (var g in _state.AudioGroups)
            AudioSourceHost.Children.Add(BuildSourceCard(g, isVideo: false));
        if (_state.AudioGroups.Count == 0)
            AudioSourceHost.Children.Add(new TextBlock { Text = "No audio", Opacity = 0.5 });

        FitIconHost.Children.Clear();
        FitDockLabel.Text = _state.FitLabel;
        FitDockTime.Text = _state.FitReferenceIso;
        if (_state.FitPath is not null)
        {
            FitIconHost.Children.Add(new TextBlock
            {
                Text = "📍 " + Path.GetFileName(_state.FitPath),
                FontSize = 12
            });
        }
    }

    private UIElement BuildSourceCard(DeviceGroup group, bool isVideo)
    {
        var card = new StackPanel { Spacing = 6, Padding = new Thickness(8) };
        card.Children.Add(new TextBlock { Text = group.Label, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
        card.Children.Add(new TextBlock
        {
            Text = "Device clock: " + group.DeviceClockIso,
            FontSize = 11,
            Opacity = 0.7,
            FontFamily = new Microsoft.UI.Xaml.Media.FontFamily("Consolas")
        });
        foreach (var clip in group.Clips.ToList())
        {
            var row = new StackPanel { Spacing = 4, Padding = new Thickness(6) };
            var nameRow = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            nameRow.Children.Add(new TextBlock
            {
                Text = (isVideo ? "🎬 " : "🔊 ") + Path.GetFileName(clip.Path),
                FontSize = 12,
                VerticalAlignment = VerticalAlignment.Center
            });
            var remove = new Button
            {
                Content = "×",
                Tag = new ClipRef { Group = group, Clip = clip, IsVideo = isVideo },
                Padding = new Thickness(6, 0, 6, 0)
            };
            remove.Click += RemoveClip_Click;
            nameRow.Children.Add(remove);
            row.Children.Add(nameRow);

            var startBox = new TextBox
            {
                Header = "Start (local ISO)",
                Text = clip.StartIso,
                Tag = clip,
                FontFamily = new Microsoft.UI.Xaml.Media.FontFamily("Consolas"),
                FontSize = 11
            };
            startBox.TextChanged += (s, _) =>
            {
                if (s is TextBox tb && tb.Tag is MediaClip c) c.StartIso = tb.Text ?? c.StartIso;
            };
            row.Children.Add(startBox);

            var durBox = new NumberBox
            {
                Header = "Duration (s)",
                Value = clip.Duration,
                Minimum = 0.1,
                SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Inline,
                Tag = clip,
                Width = 140
            };
            durBox.ValueChanged += (s, args) =>
            {
                if (s is NumberBox nb && nb.Tag is MediaClip c && !double.IsNaN(args.NewValue))
                    c.Duration = args.NewValue;
            };
            row.Children.Add(durBox);

            if (!string.Equals(clip.MetadataStartIso, clip.StartIso, StringComparison.Ordinal))
            {
                row.Children.Add(new TextBlock
                {
                    Text = "meta " + clip.MetadataStartIso,
                    FontSize = 10,
                    Opacity = 0.5,
                    FontFamily = new Microsoft.UI.Xaml.Media.FontFamily("Consolas")
                });
            }
            card.Children.Add(row);
        }
        return card;
    }

    private void RemoveClip_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button { Tag: ClipRef tag }) return;
        tag.Group.Clips.Remove(tag.Clip);
        var list = tag.IsVideo ? _state.CameraGroups : _state.AudioGroups;
        list.RemoveAll(g => g.Clips.Count == 0);
        RefreshMediaDock();
    }

    sealed class ClipRef
    {
        public DeviceGroup Group { get; init; } = null!;
        public MediaClip Clip { get; init; } = null!;
        public bool IsVideo { get; init; }
    }

    private void MapToggle_Toggled(object sender, RoutedEventArgs e)
    {
        _state.IncludeMap = MapToggle.IsOn;
    }

    private async void AddVideos_Click(object sender, RoutedEventArgs e)
    {
        await PickVideosAsync(replace: false);
        await ProbeCamerasAsync(applyOffsets: _state.Step == WizardStep.Ready);
    }

    private async void AddAudio_Click(object sender, RoutedEventArgs e)
    {
        await PickAudioAsync();
        if (_state.AllAudioClips.Count > 0)
            await ProbeAudioAsync(applyOffsets: _state.Step == WizardStep.Ready);
    }

    private async void ChangeFit_Click(object sender, RoutedEventArgs e)
    {
        await PickFitAsync();
        if (_state.Step == WizardStep.SyncFit)
            ShowWizard();
    }

    private async void DryRun_Click(object sender, RoutedEventArgs e) => await RunCompileAsync(true);
    private async void Compile_Click(object sender, RoutedEventArgs e) => await RunCompileAsync(false);

    private async Task RunCompileAsync(bool dryRun)
    {
        if (_state.FitPath is null || _state.AllVideoClips.Count == 0)
        {
            StatusText.Text = "FIT and videos required";
            return;
        }
        var selectYaml = ConfigBuilder.SelectYaml(_state);
        var overlayYaml = ConfigBuilder.OverlayYaml(_state);
        var syncYaml = ConfigBuilder.SyncYaml(_state);
        var tmp = Path.GetTempPath();
        var selectPath = Path.Combine(tmp, $"fitvid-select-{Guid.NewGuid()}.yaml");
        var overlayPath = Path.Combine(tmp, $"fitvid-overlay-{Guid.NewGuid()}.yaml");
        var syncPath = Path.Combine(tmp, $"fitvid-sync-{Guid.NewGuid()}.yaml");
        await File.WriteAllTextAsync(selectPath, selectYaml);
        await File.WriteAllTextAsync(overlayPath, overlayYaml);
        await File.WriteAllTextAsync(syncPath, syncYaml);
        var outPath = string.IsNullOrWhiteSpace(OutputBox.Text)
            ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Desktop), "highlight.mp4")
            : OutputBox.Text;
        try
        {
            StatusText.Text = dryRun ? "Dry run…" : "Generating…";
            var videos = _state.AllVideoClips.ToList();
            var audios = _state.AllAudioClips.ToList();
            await Task.Run(() => _cli.Compile(
                _state.FitPath,
                videos,
                audios,
                selectPath,
                overlayPath,
                syncPath,
                outPath,
                PadBefore.Value,
                PadAfter.Value,
                dryRun,
                line => DispatcherQueue.TryEnqueue(() => AppendLog(line))
            ));
            StatusText.Text = dryRun ? "Dry run complete" : $"Wrote {outPath}";
        }
        catch (Exception ex)
        {
            StatusText.Text = ex.Message;
            AppendLog("Error: " + ex.Message);
        }
    }

    private void AppendLog(string line) => LogBox.Text += line + Environment.NewLine;

    private void Mode_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (ModeBox.SelectedItem is ComboBoxItem item)
        {
            _state.Mode = item.Content?.ToString() switch
            {
                "All videos" => SelectMode.Videos,
                "Threshold" => SelectMode.Threshold,
                "Manual" => SelectMode.Manual,
                "Laps" => SelectMode.Laps,
                _ => SelectMode.Videos
            };
            ThresholdPanel.Visibility = _state.Mode == SelectMode.Threshold ? Visibility.Visible : Visibility.Collapsed;
        }
    }
}

enum WizardStep { Fit, SyncFit, Videos, SyncCameras, Audio, SyncAudio, Ready }
enum SelectMode { Videos, Laps, Threshold, Manual }

sealed class AppState
{
    public WizardStep Step { get; set; } = WizardStep.Fit;
    public string? FitPath { get; set; }
    public InspectPayload? Inspect { get; set; }
    public HashSet<string> SelectedFields { get; set; } = new();
    public bool IncludeMap { get; set; }
    public SelectMode Mode { get; set; } = SelectMode.Videos;
    public string? ThresholdField { get; set; }
    public string ThresholdOp { get; set; } = ">";
    public double ThresholdValue { get; set; }
    public double ThresholdMinDuration { get; set; } = 5;
    public string FitLabel { get; set; } = "FIT device";
    public string FitReferenceIso { get; set; } = "";
    public string WatchClockIso { get; set; } = "";
    public string WallClockIso { get; set; } = "";
    public List<DeviceGroup> CameraGroups { get; set; } = new();
    public List<DeviceGroup> AudioGroups { get; set; } = new();
    public IEnumerable<MediaClip> AllVideoClips => CameraGroups.SelectMany(g => g.Clips);
    public IEnumerable<MediaClip> AllAudioClips => AudioGroups.SelectMany(g => g.Clips);
}

sealed class MediaClip
{
    public string Path { get; set; } = "";
    public string MetadataStartIso { get; set; } = "";
    public string StartIso { get; set; } = "";
    public double Duration { get; set; }

    public static MediaClip Placeholder(string path, string fallbackStart) => new()
    {
        Path = path,
        MetadataStartIso = fallbackStart,
        StartIso = fallbackStart,
        Duration = 0
    };
}

sealed class DeviceGroup
{
    public string Id { get; set; } = "";
    public string Label { get; set; } = "";
    public List<MediaClip> Clips { get; set; } = new();
    public string DeviceClockIso { get; set; } = "";

    public static List<DeviceGroup> FromProbe(
        List<ProbedDevice> devices,
        string fallbackClock,
        string kind,
        string idSuffix = "")
    {
        return devices.Select((d, i) =>
        {
            var id = string.IsNullOrWhiteSpace(d.Id) ? $"{kind}-{i}" : d.Id;
            if (!string.IsNullOrEmpty(idSuffix) && !id.EndsWith(idSuffix, StringComparison.Ordinal))
                id += idSuffix;
            var probeByPath = d.Probes
                .Where(p => !string.IsNullOrWhiteSpace(p.Path))
                .GroupBy(p => p.Path!, StringComparer.OrdinalIgnoreCase)
                .ToDictionary(g => g.Key, g => g.First(), StringComparer.OrdinalIgnoreCase);
            var files = d.Files.Count > 0 ? d.Files : probeByPath.Keys.ToList();
            var clips = files.Select(path =>
            {
                probeByPath.TryGetValue(path, out var probe);
                var start = probe?.StartTime ?? fallbackClock;
                return new MediaClip
                {
                    Path = path,
                    MetadataStartIso = start,
                    StartIso = start,
                    Duration = probe?.Duration ?? 0
                };
            }).ToList();
            return new DeviceGroup
            {
                Id = id,
                Label = string.IsNullOrWhiteSpace(d.Label) ? id : d.Label,
                DeviceClockIso = fallbackClock,
                Clips = clips
            };
        }).ToList();
    }
}

static class LocalTimeSync
{
    public static DateTimeOffset? Parse(string? s)
    {
        if (string.IsNullOrWhiteSpace(s)) return null;
        if (DateTimeOffset.TryParse(s, CultureInfo.InvariantCulture,
                DateTimeStyles.AllowWhiteSpaces, out var dto))
            return dto.ToLocalTime();
        return null;
    }

    public static string Format(DateTimeOffset dto) =>
        dto.ToLocalTime().ToString("yyyy-MM-dd'T'HH:mm:sszzz", CultureInfo.InvariantCulture);

    public static string SpineIso(AppState s)
    {
        if (!string.IsNullOrWhiteSpace(s.WallClockIso)) return s.WallClockIso;
        var watch = string.IsNullOrWhiteSpace(s.WatchClockIso) ? s.FitReferenceIso : s.WatchClockIso;
        var refT = Parse(s.FitReferenceIso);
        var watchT = Parse(watch);
        if (refT is null) return s.FitReferenceIso;
        if (watchT is null) return Format(refT.Value);
        var delta = watchT.Value - refT.Value;
        return Format(refT.Value + delta);
    }

    public static void ApplyOffsets(AppState state, List<DeviceGroup> groups)
    {
        var spine = SpineIso(state);
        var spineT = Parse(spine);
        foreach (var g in groups)
        {
            var deviceClock = string.IsNullOrWhiteSpace(g.DeviceClockIso) ? spine : g.DeviceClockIso;
            var deviceT = Parse(deviceClock);
            foreach (var clip in g.Clips)
            {
                var meta = Parse(clip.MetadataStartIso);
                if (meta is null || spineT is null || deviceT is null)
                {
                    clip.StartIso = clip.MetadataStartIso;
                    continue;
                }
                var offset = spineT.Value - deviceT.Value;
                clip.StartIso = Format(meta.Value + offset);
            }
        }
    }
}

sealed class InspectPayload
{
    [JsonPropertyName("sport")] public string Sport { get; set; } = "";
    [JsonPropertyName("has_gps")] public bool HasGps { get; set; }
    [JsonPropertyName("session_start")] public string? SessionStart { get; set; }
    [JsonPropertyName("session_end")] public string? SessionEnd { get; set; }
    [JsonPropertyName("fit_device")] public FitDeviceInfo? FitDevice { get; set; }
    [JsonPropertyName("fields")] public List<InspectField> Fields { get; set; } = new();
}

sealed class FitDeviceInfo
{
    [JsonPropertyName("label")] public string? Label { get; set; }
}

sealed class InspectField
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("label")] public string Label { get; set; } = "";
    [JsonPropertyName("category")] public string Category { get; set; } = "other";
    [JsonPropertyName("unit")] public string? Unit { get; set; }
    [JsonPropertyName("min")] public double? Min { get; set; }
    [JsonPropertyName("max")] public double? Max { get; set; }
}

static class ConfigBuilder
{
    static readonly Dictionary<string, string> Formats = new()
    {
        ["speed"] = "{value:.1f} km/h",
        ["heart_rate"] = "{value:.0f} bpm",
        ["grade"] = "{value:.1f}%",
        ["power"] = "{value:.0f} W",
        ["cadence"] = "{value:.0f}",
        ["altitude"] = "{value:.0f} m",
    };

    public static string OverlayYaml(AppState s)
    {
        var sb = new StringBuilder("overlay:\n  text:\n");
        var selected = s.Inspect?.Fields.Where(f => s.SelectedFields.Contains(f.Name)).ToList() ?? [];
        if (selected.Count == 0) sb.AppendLine("    []");
        else
        {
            for (var i = 0; i < selected.Count; i++)
            {
                var f = selected[i];
                var fmt = Formats.GetValueOrDefault(f.Name, "{value}");
                var y = Math.Max(0.04, 0.88 - i * 0.065);
                sb.AppendLine($"    - field: {f.Name}");
                sb.AppendLine($"      format: \"{fmt}\"");
                sb.AppendLine($"      label: \"{f.Label}\"");
                sb.AppendLine($"      position: [0.02, {y:0.000}]");
            }
        }
        if (s.IncludeMap && s.Inspect?.HasGps == true)
        {
            sb.AppendLine("  map:");
            sb.AppendLine("    style: route-only");
            sb.AppendLine("    anchor: bottom-right");
            sb.AppendLine("    width_px: 280");
            sb.AppendLine("    height_px: 280");
        }
        return sb.ToString();
    }

    public static string SelectYaml(AppState s) => s.Mode switch
    {
        SelectMode.Videos => "select:\n  - type: videos\n",
        SelectMode.Threshold => $"""
            select:
              - type: threshold
                field: {s.ThresholdField ?? "speed"}
                op: "{s.ThresholdOp}"
                value: {s.ThresholdValue}
                min_duration: {s.ThresholdMinDuration}
            """,
        _ => "select:\n  - type: laps\n"
    };

    public static string SyncYaml(AppState s)
    {
        var sb = new StringBuilder();
        sb.AppendLine("sync:");
        sb.AppendLine("  fit_generator:");
        sb.AppendLine($"    label: \"{Escape(s.FitLabel)}\"");
        sb.AppendLine($"    fit_reference: \"{Escape(s.FitReferenceIso)}\"");
        sb.AppendLine($"    watch_clock: \"{Escape(string.IsNullOrWhiteSpace(s.WatchClockIso) ? s.FitReferenceIso : s.WatchClockIso)}\"");
        if (string.IsNullOrWhiteSpace(s.WallClockIso))
            sb.AppendLine("    wall_clock: null");
        else
            sb.AppendLine($"    wall_clock: \"{Escape(s.WallClockIso)}\"");
        sb.AppendLine("  devices:");
        var any = false;
        foreach (var cam in s.CameraGroups)
        {
            any = true;
            AppendDevice(sb, cam, "video");
        }
        foreach (var rec in s.AudioGroups)
        {
            any = true;
            AppendDevice(sb, rec, "audio");
        }
        if (!any) sb.AppendLine("    []");
        return sb.ToString();
    }

    static void AppendDevice(StringBuilder sb, DeviceGroup d, string kind)
    {
        sb.AppendLine($"    - id: {Escape(d.Id)}");
        sb.AppendLine($"      kind: {kind}");
        sb.AppendLine($"      label: \"{Escape(d.Label)}\"");
        // Starts already corrected in the UI via --video-start / --audio-start
        sb.AppendLine("      offset_seconds: 0");
        sb.AppendLine("      files:");
        foreach (var f in d.Clips)
            sb.AppendLine($"        - {Escape(f.Path)}");
    }

    static string Escape(string s)
    {
        var cleaned = new string(s.Where(ch => ch is '\n' or '\r' or '\t' || ch >= 0x20).ToArray());
        return cleaned.Replace("\"", "\\\"");
    }
}

sealed class ProbeMediaPayload
{
    [JsonPropertyName("devices")] public List<ProbedDevice> Devices { get; set; } = new();
}

sealed class ProbedDevice
{
    [JsonPropertyName("id")] public string Id { get; set; } = "";
    [JsonPropertyName("label")] public string Label { get; set; } = "";
    [JsonPropertyName("files")] public List<string> Files { get; set; } = new();
    [JsonPropertyName("probes")] public List<ProbedFile> Probes { get; set; } = new();
}

sealed class ProbedFile
{
    [JsonPropertyName("path")] public string? Path { get; set; }
    [JsonPropertyName("start_time")] public string? StartTime { get; set; }
    [JsonPropertyName("duration")] public double? Duration { get; set; }
}

sealed class FitvidCli
{
    readonly string _binary;

    public FitvidCli()
    {
        var baseDir = AppContext.BaseDirectory;
        var bundled = Path.Combine(baseDir, "resources", "fitvid", "fitvid.exe");
        _binary = File.Exists(bundled) ? bundled : "fitvid";
    }

    public InspectPayload Inspect(string fitPath)
    {
        var (code, stdout, stderr) = Run(["inspect", fitPath, "--format", "json"]);
        if (code != 0) throw new InvalidOperationException(string.IsNullOrWhiteSpace(stderr) ? stdout : stderr);
        return JsonSerializer.Deserialize<InspectPayload>(stdout)
               ?? throw new InvalidOperationException("empty inspect payload");
    }

    public List<ProbedDevice> ProbeMedia(List<string> paths)
    {
        var args = new List<string> { "probe-media", "--group" };
        args.AddRange(paths);
        var (code, stdout, stderr) = Run(args);
        if (code != 0) throw new InvalidOperationException(string.IsNullOrWhiteSpace(stderr) ? stdout : stderr);
        var payload = JsonSerializer.Deserialize<ProbeMediaPayload>(stdout)
                      ?? throw new InvalidOperationException("empty probe-media payload");
        return payload.Devices;
    }

    public void Compile(
        string fit,
        List<MediaClip> videos,
        List<MediaClip> audios,
        string selectYaml,
        string overlayYaml,
        string syncYaml,
        string output,
        double padBefore,
        double padAfter,
        bool dryRun,
        Action<string> onLine)
    {
        var args = new List<string>
        {
            "compile", "--fit", fit, "--select", selectYaml, "--overlay", overlayYaml,
            "--sync-config", syncYaml,
            "--out", output, "--pad-before", padBefore.ToString(CultureInfo.InvariantCulture),
            "--pad-after", padAfter.ToString(CultureInfo.InvariantCulture),
            "--json-events", "--sync", "none"
        };
        foreach (var v in videos)
        {
            args.Add("--video"); args.Add(v.Path);
            if (!string.IsNullOrWhiteSpace(v.StartIso))
            {
                args.Add("--video-start"); args.Add(v.StartIso);
            }
            args.Add("--video-duration");
            args.Add(v.Duration.ToString(CultureInfo.InvariantCulture));
        }
        foreach (var a in audios)
        {
            args.Add("--audio"); args.Add(a.Path);
            if (!string.IsNullOrWhiteSpace(a.StartIso))
            {
                args.Add("--audio-start"); args.Add(a.StartIso);
            }
            args.Add("--audio-duration");
            args.Add(a.Duration.ToString(CultureInfo.InvariantCulture));
        }
        if (dryRun) args.Add("--dry-run");
        var (code, _, stderr) = Run(args, onLine);
        if (code != 0) throw new InvalidOperationException(string.IsNullOrWhiteSpace(stderr) ? "generate failed" : stderr);
    }

    (int code, string stdout, string stderr) Run(IEnumerable<string> args, Action<string>? onLine = null)
    {
        var psi = new ProcessStartInfo
        {
            FileName = _binary,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        foreach (var a in args) psi.ArgumentList.Add(a);
        using var p = Process.Start(psi) ?? throw new InvalidOperationException("failed to start fitvid");
        var sb = new StringBuilder();
        string? line;
        while ((line = p.StandardOutput.ReadLine()) is not null)
        {
            sb.AppendLine(line);
            onLine?.Invoke(line);
        }
        var err = p.StandardError.ReadToEnd();
        p.WaitForExit();
        return (p.ExitCode, sb.ToString(), err);
    }
}
