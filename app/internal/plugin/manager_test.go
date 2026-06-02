package plugin

import (
	"context"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"

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
	entered := make(chan struct{})
	release := make(chan struct{})
	var once sync.Once
	startManagedProcess = func(context.Context, platform.CommandSpec) (platform.ManagedProcess, error) {
		atomic.AddInt32(&launches, 1)
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

type alwaysAliveProcess struct{}

func (alwaysAliveProcess) PID() int                   { return 1 }
func (alwaysAliveProcess) Alive() bool                { return true }
func (alwaysAliveProcess) Wait() error                { return nil }
func (alwaysAliveProcess) Stop(context.Context) error { return nil }
