package plugin

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"

	"smartclassdownloader/internal/applog"
	"smartclassdownloader/internal/aria2"
	"smartclassdownloader/internal/domain"
	"smartclassdownloader/internal/platform"
)

type Status struct {
	Installed    bool          `json:"installed"`
	Running      bool          `json:"running"`
	Installing   bool          `json:"installing"`
	Uninstalling bool          `json:"uninstalling"`
	ModelStatus  RuntimeStatus `json:"model_status"`
}

type runningPlugin struct {
	process platform.ManagedProcess
	port    int
}

type startAttempt struct {
	done chan struct{}
	err  error
}

type Manager struct {
	mu           sync.Mutex
	layout       platform.Layout
	registry     *Registry
	statuses     *StatusStore
	installer    *Installer
	aria2        *aria2.ProcessManager
	processes    map[string]runningPlugin
	starting     map[string]*startAttempt
	uninstalling map[string]bool
	mainURL      string
	updateTask   func(string, domain.TaskUpdate)
	startupTask  func(string) string
	appContext   context.Context
}

var logger = applog.Get("plugin")
var startManagedProcess = platform.StartManagedProcess

func NewManager(ctx context.Context, layout platform.Layout, registry *Registry, statuses *StatusStore, installer *Installer, aria2Process *aria2.ProcessManager) *Manager {
	return &Manager{
		layout: layout, registry: registry, statuses: statuses, installer: installer,
		aria2: aria2Process, processes: map[string]runningPlugin{},
		starting: map[string]*startAttempt{}, uninstalling: map[string]bool{}, appContext: ctx,
	}
}

func (manager *Manager) SetCallbacks(update func(string, domain.TaskUpdate), startup func(string) string) {
	manager.updateTask = update
	manager.startupTask = startup
}

func (manager *Manager) SetMainServerURL(value string) {
	manager.mu.Lock()
	manager.mainURL = value
	manager.mu.Unlock()
}
func (manager *Manager) Registry() *Registry { return manager.registry }
func (manager *Manager) Install(ctx context.Context, id string, report func(string)) error {
	return manager.installer.Install(ctx, id, report)
}
func (manager *Manager) IsInstalled(id string) bool { return manager.installer.IsInstalled(id) }

func (manager *Manager) Status(ctx context.Context, id string, probe bool) Status {
	manager.mu.Lock()
	running := manager.processes[id]
	uninstalling := manager.uninstalling[id]
	if running.process != nil && !running.process.Alive() {
		delete(manager.processes, id)
		running = runningPlugin{}
	}
	manager.mu.Unlock()
	isRunning := running.process != nil && running.process.Alive()
	if isRunning && probe {
		isRunning = manager.healthy(ctx, id, running.port)
	}
	return Status{
		Installed: manager.installer.IsInstalled(id), Running: isRunning,
		Uninstalling: uninstalling, ModelStatus: manager.statuses.Read(id),
	}
}

func (manager *Manager) ServiceURL(id string) (string, bool) {
	definition, ok := manager.registry.Get(id)
	if !ok {
		return "", false
	}
	manager.mu.Lock()
	running := manager.processes[id]
	manager.mu.Unlock()
	port := definition.DefaultPort
	if running.port != 0 {
		port = running.port
	}
	return fmt.Sprintf("http://127.0.0.1:%d", port), running.process != nil && running.process.Alive()
}

func (manager *Manager) StartupError(id string) error {
	status := manager.statuses.Read(id)
	if status.Phase == "failed" {
		message := status.Error
		if message == "" {
			message = status.Message
		}
		if message == "" {
			message = "插件上报启动失败"
		}
		return fmt.Errorf("%s 启动失败: %s", id, message)
	}
	manager.mu.Lock()
	running := manager.processes[id]
	manager.mu.Unlock()
	if running.process != nil && !running.process.Alive() {
		return fmt.Errorf("%s 进程意外退出", id)
	}
	return nil
}

func (manager *Manager) Start(ctx context.Context, id string) (bool, error) {
	definition, ok := manager.registry.Get(id)
	if !ok {
		return false, fmt.Errorf("未知插件: %s", id)
	}
	if !manager.installer.IsInstalled(id) {
		return false, fmt.Errorf("%s 未安装", id)
	}
	manager.mu.Lock()
	if current := manager.processes[id]; current.process != nil && current.process.Alive() {
		manager.mu.Unlock()
		return false, nil
	}
	if attempt := manager.starting[id]; attempt != nil {
		manager.mu.Unlock()
		select {
		case <-attempt.done:
			return false, attempt.err
		case <-ctx.Done():
			return false, ctx.Err()
		}
	}
	attempt := &startAttempt{done: make(chan struct{})}
	manager.starting[id] = attempt
	manager.mu.Unlock()
	err := manager.start(ctx, id, definition)
	manager.mu.Lock()
	delete(manager.starting, id)
	attempt.err = err
	close(attempt.done)
	manager.mu.Unlock()
	return err == nil, err
}

func (manager *Manager) start(ctx context.Context, id string, definition Definition) error {
	port, err := platform.FindAvailablePort("127.0.0.1", definition.DefaultPort)
	if err != nil {
		return err
	}
	manager.mu.Lock()
	mainURL := manager.mainURL
	manager.mu.Unlock()
	extra := map[string]string{"MAIN_SERVER_URL": mainURL}
	if manager.registry.HasCapability(id, "model_download") {
		_ = manager.statuses.Clear(id)
		if manager.aria2 == nil {
			return fmt.Errorf("aria2 manager is unavailable")
		}
		if id == "whisper" {
			binary, err := manager.aria2.Binary(ctx)
			if err != nil {
				return err
			}
			extra["ARIA2C_PATH"] = binary
			extra["ARIA2_RPC_URL"] = ""
			extra["ARIA2_RPC_SECRET"] = ""
			extra["MODEL_NETWORK_MODE"] = "system_proxy"
		} else {
			ariaEnv, err := manager.aria2.Environment(ctx)
			if err != nil {
				return err
			}
			for key, value := range ariaEnv {
				extra[key] = value
			}
		}
		extra["PLUGIN_STATUS_FILE"] = manager.layout.PluginStatusFile(id)
		manager.statuses.Update(id, RuntimeStatus{Phase: "initializing", Message: "正在启动插件进程并检查模型文件..."})
	}
	logPath := filepath.Join(manager.layout.LogsDir, id+"_run.log")
	if err := applog.RotateIfNeeded(logPath, applog.DefaultMaxBytes, applog.DefaultBackups); err != nil {
		return fmt.Errorf("rotate plugin log: %w", err)
	}
	logFile, err := os.OpenFile(logPath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	python := filepath.Join(manager.layout.PluginVenv(definition.Venv), "Scripts", "python.exe")
	entry := filepath.Join(manager.layout.PluginsDir, definition.Folder, definition.Entry)
	environment := platform.BaseEnvironment(extra)
	if extra["MODEL_NETWORK_MODE"] == "direct" {
		environment = platform.DirectEnvironment(extra)
	}
	process, err := startManagedProcess(manager.appContext, platform.CommandSpec{
		Path: python, Args: []string{entry, "--port", fmt.Sprint(port)}, Env: environment,
		Dir: filepath.Dir(entry), Stdout: logFile, Stderr: logFile, Hidden: true, KeepStdin: true,
	})
	_ = logFile.Close()
	if err != nil {
		return err
	}
	manager.mu.Lock()
	manager.processes[id] = runningPlugin{process: process, port: port}
	manager.mu.Unlock()
	logger.Infof("started plugin id=%s port=%d log=%s", id, port, logPath)
	if manager.startupTask == nil {
		return nil
	}
	manager.startupTask(id)
	if manager.registry.HasCapability(id, "model_download") {
		manager.applyStartup(id, manager.statuses.Read(id))
		go manager.watchStartup(id, process)
	} else {
		status := RuntimeStatus{Phase: "initializing", Message: "正在启动 PPT 提取服务...", Progress: 10}
		manager.statuses.Update(id, status)
		manager.applyStartup(id, status)
		go manager.watchServiceStartup(id, process, port)
	}
	return nil
}

func (manager *Manager) watchServiceStartup(id string, process platform.ManagedProcess, port int) {
	ticker := time.NewTicker(250 * time.Millisecond)
	defer ticker.Stop()
	timeout := time.NewTimer(time.Hour)
	defer timeout.Stop()
	for {
		if !process.Alive() {
			manager.finishServiceStartup(id, RuntimeStatus{Phase: "failed", Message: id + " 进程意外退出"})
			return
		}
		if manager.healthy(manager.appContext, id, port) {
			manager.finishServiceStartup(id, RuntimeStatus{Phase: "ready", Message: "PPT 提取服务已就绪", Progress: 100})
			return
		}
		select {
		case <-manager.appContext.Done():
			return
		case <-ticker.C:
		case <-timeout.C:
			manager.finishServiceStartup(id, RuntimeStatus{Phase: "failed", Message: id + " 启动超时"})
			return
		}
	}
}

func (manager *Manager) finishServiceStartup(id string, status RuntimeStatus) {
	manager.statuses.Update(id, status)
	if status.Phase == "ready" {
		logger.Infof("plugin service ready id=%s", id)
	} else {
		logger.Errorf("plugin service startup failed id=%s: %s", id, status.Message)
	}
	manager.applyStartup(id, status)
}

func (manager *Manager) Stop(ctx context.Context, id string) error {
	manager.mu.Lock()
	current := manager.processes[id]
	delete(manager.processes, id)
	manager.mu.Unlock()
	_ = manager.statuses.Clear(id)
	if current.process == nil {
		return nil
	}
	if err := current.process.Stop(ctx); err != nil {
		logger.Errorf("stop plugin id=%s: %v", id, err)
		return err
	}
	logger.Infof("stopped plugin id=%s", id)
	return nil
}

func (manager *Manager) StopAll(ctx context.Context) error {
	manager.mu.Lock()
	ids := make([]string, 0, len(manager.processes))
	for id := range manager.processes {
		ids = append(ids, id)
	}
	manager.mu.Unlock()
	sort.Strings(ids)
	var lastErr error
	for _, id := range ids {
		if err := manager.Stop(ctx, id); err != nil {
			lastErr = err
		}
	}
	return lastErr
}

func (manager *Manager) Uninstall(ctx context.Context, id string) error {
	if _, ok := manager.registry.Get(id); !ok {
		return fmt.Errorf("未知插件: %s", id)
	}
	manager.mu.Lock()
	if manager.uninstalling[id] {
		manager.mu.Unlock()
		return fmt.Errorf("该插件正在卸载中，请稍候")
	}
	manager.uninstalling[id] = true
	manager.mu.Unlock()
	defer func() {
		manager.mu.Lock()
		delete(manager.uninstalling, id)
		manager.mu.Unlock()
	}()
	_ = manager.Stop(ctx, id)
	if err := manager.installer.Uninstall(ctx, id); err != nil {
		logger.Errorf("uninstall plugin id=%s: %v", id, err)
		return err
	}
	logger.Infof("uninstalled plugin id=%s", id)
	return nil
}

func (manager *Manager) AcceptReport(id string, body map[string]any) error {
	if _, ok := manager.registry.Get(id); !ok {
		return fmt.Errorf("未知插件: %s", id)
	}
	if manager.registry.HasCapability(id, "model_download") && manager.startupTask != nil {
		manager.startupTask(id)
	}
	status := runtimeStatusFromMap(body)
	manager.statuses.Update(id, status)
	logger.Debugf("plugin startup status id=%s phase=%s progress=%.1f message=%s", id, status.Phase, status.Progress, status.Message)
	manager.applyStartup(id, status)
	return nil
}

func (manager *Manager) watchStartup(id string, process platform.ManagedProcess) {
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()
	timeout := time.After(time.Hour)
	for {
		select {
		case <-ticker.C:
			if !process.Alive() {
				manager.applyStartup(id, RuntimeStatus{Phase: "failed", Message: id + " 进程意外退出"})
				return
			}
			status := manager.statuses.Read(id)
			if status.Phase != "" && manager.applyStartup(id, status) {
				return
			}
		case <-timeout:
			manager.applyStartup(id, RuntimeStatus{Phase: "failed", Message: id + " 启动超时"})
			return
		}
	}
}

func (manager *Manager) applyStartup(id string, status RuntimeStatus) bool {
	if manager.updateTask == nil {
		return status.Phase == "ready" || status.Phase == "failed"
	}
	taskID := "startup_" + id
	update := domain.TaskUpdate{
		Progress: domain.Float(status.Progress), TotalSize: domain.Int64(status.TotalSize),
		DownloadedSize: domain.Int64(status.DownloadedSize), Speed: domain.Float(status.Speed),
		Message: domain.String(status.Message),
	}
	action := "启动服务"
	switch status.Phase {
	case "checking":
		action = "检查模型文件"
		update.TotalSize = domain.Int64(0)
		update.DownloadedSize = domain.Int64(0)
		update.Speed = domain.Float(0)
	case "downloading":
		action = "下载模型"
	case "loading":
		action = "加载模型"
		update.TotalSize = domain.Int64(0)
		update.DownloadedSize = domain.Int64(0)
		update.Speed = domain.Float(0)
	case "ready":
		completed := domain.TaskCompleted
		update.Status = &completed
		update.Progress = domain.Float(100)
		update.TotalSize = domain.Int64(0)
		update.DownloadedSize = domain.Int64(0)
		update.Speed = domain.Float(0)
	case "failed":
		failed := domain.TaskFailed
		update.Status = &failed
		update.TotalSize = domain.Int64(0)
		update.DownloadedSize = domain.Int64(0)
		update.Speed = domain.Float(0)
		update.Error = domain.String(status.Error)
		if status.Error == "" {
			update.Error = domain.String(status.Message)
		}
	case "initializing":
		update.TotalSize = domain.Int64(0)
		update.DownloadedSize = domain.Int64(0)
		update.Speed = domain.Float(0)
	}
	update.CurrentAction = domain.String(action)
	manager.updateTask(taskID, update)
	return status.Phase == "ready" || status.Phase == "failed"
}

func (manager *Manager) healthy(ctx context.Context, id string, port int) bool {
	definition, ok := manager.registry.Get(id)
	if !ok {
		return false
	}
	target := fmt.Sprintf("http://127.0.0.1:%d%s", port, definition.HealthPath)
	request, _ := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
	client := http.Client{Timeout: time.Second}
	response, err := client.Do(request)
	if err != nil {
		return false
	}
	_ = response.Body.Close()
	return response.StatusCode >= 200 && response.StatusCode < 500
}

func DecodeReport(request *http.Request) (map[string]any, error) {
	defer request.Body.Close()
	var body map[string]any
	err := json.NewDecoder(request.Body).Decode(&body)
	return body, err
}

func LogStatus(status Status) {
	logger.Infof("plugin status: installed=%t running=%t", status.Installed, status.Running)
}
