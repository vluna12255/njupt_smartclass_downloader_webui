package plugin

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"smartclassdownloader/internal/aria2"
	"smartclassdownloader/internal/config"
	"smartclassdownloader/internal/domain"
	"smartclassdownloader/internal/platform"
)

func TestApplyStartupCheckingClearsDownloadProgress(t *testing.T) {
	var update domain.TaskUpdate
	manager := &Manager{updateTask: func(_ string, value domain.TaskUpdate) {
		update = value
	}}

	manager.applyStartup("whisper", RuntimeStatus{
		Phase: "checking", Message: "正在检查模型文件", Progress: 42,
		TotalSize: 100, DownloadedSize: 50, Speed: 1024,
	})

	if update.CurrentAction == nil || *update.CurrentAction != "检查模型文件" {
		t.Fatalf("CurrentAction = %v, want 检查模型文件", update.CurrentAction)
	}
	if update.TotalSize == nil || *update.TotalSize != 0 {
		t.Fatalf("TotalSize = %v, want 0", update.TotalSize)
	}
	if update.DownloadedSize == nil || *update.DownloadedSize != 0 {
		t.Fatalf("DownloadedSize = %v, want 0", update.DownloadedSize)
	}
	if update.Speed == nil || *update.Speed != 0 {
		t.Fatalf("Speed = %v, want 0", update.Speed)
	}
}

func TestConcurrentStartLaunchesPluginOnce(t *testing.T) {
	layout := platform.Layout{
		LogsDir:         filepath.Join(t.TempDir(), "logs"),
		PluginsDir:      filepath.Join(t.TempDir(), "plugins"),
		PluginsEnvDir:   filepath.Join(t.TempDir(), "envs"),
		PluginStatusDir: filepath.Join(t.TempDir(), "status"),
	}
	if err := os.MkdirAll(filepath.Join(layout.PluginsEnvDir, "slides_env"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(layout.PluginsEnvDir, "slides_env", ".install_success"), []byte("ok"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(layout.LogsDir, 0o755); err != nil {
		t.Fatal(err)
	}
	registry, err := NewRegistry(Manifest{SchemaVersion: 1, Plugins: []Definition{{
		ID: "slides_extractor", Folder: "slides_extractor", Entry: "main.py", Venv: "slides_env", DefaultPort: 8002, HealthPath: "/docs",
	}}})
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	manager := NewManager(ctx, layout, registry, NewStatusStore(layout), NewInstaller(layout, registry), nil)
	var startupTasks int32
	manager.SetCallbacks(func(string, domain.TaskUpdate) {}, func(string) string {
		atomic.AddInt32(&startupTasks, 1)
		return "startup_slides_extractor"
	})

	original := startManagedProcess
	defer func() { startManagedProcess = original }()
	var launches int32
	var spec platform.CommandSpec
	entered := make(chan struct{})
	release := make(chan struct{})
	var once sync.Once
	startManagedProcess = func(_ context.Context, value platform.CommandSpec) (platform.ManagedProcess, error) {
		atomic.AddInt32(&launches, 1)
		spec = value
		once.Do(func() { close(entered) })
		<-release
		return alwaysAliveProcess{}, nil
	}

	type result struct {
		started bool
		err     error
	}
	results := make(chan result, 2)
	go func() {
		started, err := manager.Start(ctx, "slides_extractor")
		results <- result{started: started, err: err}
	}()
	<-entered
	go func() {
		started, err := manager.Start(ctx, "slides_extractor")
		results <- result{started: started, err: err}
	}()
	time.Sleep(20 * time.Millisecond)
	close(release)

	startedCount := 0
	for range 2 {
		value := <-results
		if value.err != nil {
			t.Fatal(value.err)
		}
		if value.started {
			startedCount++
		}
	}
	if launches != 1 {
		t.Fatalf("plugin launches = %d, want 1", launches)
	}
	if startedCount != 1 {
		t.Fatalf("successful starters = %d, want 1", startedCount)
	}
	if startupTasks != 1 {
		t.Fatalf("startup tasks = %d, want 1", startupTasks)
	}
	assertPythonUTF8Environment(t, testEnvironmentMap(spec.Env))
}

func TestStartupErrorReturnsReportedPluginFailure(t *testing.T) {
	layout := platform.Layout{PluginStatusDir: t.TempDir()}
	statuses := NewStatusStore(layout)
	statuses.Update("whisper", RuntimeStatus{Phase: "failed", Error: "model download failed"})
	manager := &Manager{statuses: statuses, processes: map[string]runningPlugin{}}

	err := manager.StartupError("whisper")

	if err == nil || err.Error() != "whisper 启动失败: model download failed" {
		t.Fatalf("StartupError() = %v, want reported plugin failure", err)
	}
}

func TestWhisperUsesDedicatedSystemProxyAria2(t *testing.T) {
	root := t.TempDir()
	layout := platform.Layout{
		LogsDir:         filepath.Join(root, "logs"),
		PluginsDir:      filepath.Join(root, "plugins"),
		PluginsEnvDir:   filepath.Join(root, "envs"),
		PluginStatusDir: filepath.Join(root, "status"),
		BinDir:          filepath.Join(root, "bin"),
	}
	for _, path := range []string{
		layout.LogsDir,
		filepath.Join(layout.PluginsDir, "whisper"),
		filepath.Join(layout.PluginsEnvDir, "whisper_env"),
		layout.PluginStatusDir,
		layout.BinDir,
	} {
		if err := os.MkdirAll(path, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(layout.PluginsEnvDir, "whisper_env", ".install_success"), []byte("ok"), 0o644); err != nil {
		t.Fatal(err)
	}
	binary := filepath.Join(layout.BinDir, "aria2c.exe")
	if err := os.WriteFile(binary, []byte("placeholder"), 0o755); err != nil {
		t.Fatal(err)
	}
	registry, err := NewRegistry(Manifest{SchemaVersion: 1, Plugins: []Definition{{
		ID: "whisper", Folder: "whisper", Entry: "main.py", Venv: "whisper_env",
		DefaultPort: 8000, HealthPath: "/status", Capabilities: []string{"model_download"},
	}}})
	if err != nil {
		t.Fatal(err)
	}
	binaryManager := aria2.NewBinaryManager(layout, fixedSettingsProvider{settings: config.Settings{Aria2Path: binary}})
	manager := NewManager(
		context.Background(), layout, registry, NewStatusStore(layout), NewInstaller(layout, registry),
		aria2.NewProcessManager(binaryManager),
	)

	original := startManagedProcess
	defer func() { startManagedProcess = original }()
	var spec platform.CommandSpec
	startManagedProcess = func(_ context.Context, value platform.CommandSpec) (platform.ManagedProcess, error) {
		spec = value
		return alwaysAliveProcess{}, nil
	}

	started, err := manager.Start(context.Background(), "whisper")
	if err != nil {
		t.Fatal(err)
	}
	if !started {
		t.Fatal("whisper was not started")
	}
	env := testEnvironmentMap(spec.Env)
	if env["MODEL_NETWORK_MODE"] != "system_proxy" {
		t.Fatalf("MODEL_NETWORK_MODE = %q, want system_proxy", env["MODEL_NETWORK_MODE"])
	}
	if env["ARIA2_RPC_URL"] != "" || env["ARIA2_RPC_SECRET"] != "" {
		t.Fatalf("whisper unexpectedly received shared aria2 RPC: %#v", env)
	}
	if env["ARIA2C_PATH"] != binary {
		t.Fatalf("ARIA2C_PATH = %q, want %q", env["ARIA2C_PATH"], binary)
	}
	assertPythonUTF8Environment(t, env)
}

type alwaysAliveProcess struct{}

func (alwaysAliveProcess) PID() int                   { return 1 }
func (alwaysAliveProcess) Alive() bool                { return true }
func (alwaysAliveProcess) Wait() error                { return nil }
func (alwaysAliveProcess) Stop(context.Context) error { return nil }

type fixedSettingsProvider struct {
	settings config.Settings
}

func (provider fixedSettingsProvider) Current() config.Settings { return provider.settings }

func testEnvironmentMap(entries []string) map[string]string {
	values := map[string]string{}
	for _, entry := range entries {
		key, value, _ := strings.Cut(entry, "=")
		values[strings.ToUpper(key)] = value
	}
	return values
}

func assertPythonUTF8Environment(t *testing.T, env map[string]string) {
	t.Helper()
	if env["PYTHONIOENCODING"] != "utf-8" {
		t.Fatalf("PYTHONIOENCODING = %q, want utf-8", env["PYTHONIOENCODING"])
	}
	if env["PYTHONUTF8"] != "1" {
		t.Fatalf("PYTHONUTF8 = %q, want 1", env["PYTHONUTF8"])
	}
}
