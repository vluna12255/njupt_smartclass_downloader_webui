package task

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"

	"smartclassdownloader/internal/applog"
	"smartclassdownloader/internal/domain"
)

type CourseRunner interface {
	Run(ctx context.Context, request domain.CourseRequest, report func(domain.TaskUpdate)) error
}

type PluginInstaller interface {
	Install(ctx context.Context, id string, report func(string)) error
}

type Manager struct {
	mu            sync.RWMutex
	root          context.Context
	store         *Store
	events        *EventBus
	cancellations *CancellationRegistry
	pipeline      CourseRunner
	installer     PluginInstaller
}

var logger = applog.Get("task")

func NewManager(root context.Context, store *Store, events *EventBus, installer PluginInstaller) *Manager {
	return &Manager{root: root, store: store, events: events, cancellations: NewCancellationRegistry(), installer: installer}
}

func (manager *Manager) SetPipeline(pipeline CourseRunner) {
	manager.mu.Lock()
	manager.pipeline = pipeline
	manager.mu.Unlock()
}

func (manager *Manager) Events() *EventBus { return manager.events }

func (manager *Manager) AddCourseTask(ctx context.Context, request domain.CourseRequest) (string, error) {
	id := manager.store.NextAvailableID(request.VideoID)
	request.TaskID = id
	item := domain.NewTask(id, request.Title, domain.TaskKindCourse)
	item.PluginDependencies = append([]string{}, request.PluginDependencies...)
	if err := manager.store.Create(item); err != nil {
		return "", err
	}
	manager.publish(item)
	child, cancel := context.WithCancel(manager.root)
	manager.cancellations.Register(id, cancel)
	logger.Infof("queued course task id=%s title=%s", id, request.Title)
	go manager.runCourseTask(child, id, request)
	return id, nil
}

func (manager *Manager) AddPluginInstallTask(ctx context.Context, pluginID string) (string, error) {
	id := "install_" + pluginID
	if existing, ok := manager.store.Get(id); ok && !existing.IsTerminal() {
		return "", fmt.Errorf("任务添加失败，可能任务已存在")
	}
	item := domain.NewTask(id, "系统: 安装 "+pluginID, domain.TaskKindPluginInstall)
	item.PluginDependencies = []string{pluginID}
	if existing, ok := manager.store.Get(id); ok && existing.IsTerminal() {
		manager.store.Replace(item)
	} else {
		if err := manager.store.Create(item); err != nil {
			return "", err
		}
	}
	child, cancel := context.WithCancel(manager.root)
	manager.cancellations.Register(id, cancel)
	logger.Infof("queued plugin install task id=%s plugin=%s", id, pluginID)
	go manager.runInstallTask(child, id, pluginID)
	return id, nil
}

func (manager *Manager) EnsurePluginStartupTask(pluginID string) string {
	id := "startup_" + pluginID
	if existing, ok := manager.store.Get(id); ok && !existing.IsTerminal() {
		return id
	}
	item := domain.NewTask(id, startupTaskTitle(pluginID), domain.TaskKindPluginStartup)
	item.Status = domain.TaskRunning
	item.CurrentAction = "启动服务"
	item.Message = "服务启动中..."
	item.PluginDependencies = []string{pluginID}
	if _, ok := manager.store.Get(id); ok {
		manager.store.Replace(item)
	} else {
		_ = manager.store.Create(item)
	}
	manager.publish(item)
	logger.Infof("tracking plugin startup task id=%s plugin=%s", id, pluginID)
	return id
}

func (manager *Manager) Update(id string, update domain.TaskUpdate) {
	item, err := manager.store.Update(id, update)
	if err == nil {
		manager.publish(item)
	} else {
		logger.Warnf("update task id=%s: %v", id, err)
	}
}

func (manager *Manager) ListViews() []domain.TaskView { return manager.store.Views() }

func (manager *Manager) AbortPluginTasks(pluginID string) {
	for _, item := range manager.store.List() {
		if item.IsTerminal() || !contains(item.PluginDependencies, pluginID) {
			continue
		}
		manager.cancellations.Cancel(item.ID)
		logger.Warnf("cancel task id=%s because plugin=%s was uninstalled", item.ID, pluginID)
		manager.Update(item.ID, domain.TaskUpdate{
			Status: domainStatus(domain.TaskCancelled), Message: domain.String("失败: 依赖插件已卸载"),
			Error: domain.String("插件 " + pluginID + " 已被卸载，任务强制中止"), Speed: domain.Float(0),
		})
	}
}

func (manager *Manager) CancelAll() { manager.cancellations.CancelAll() }
func (manager *Manager) Close() {
	manager.CancelAll()
	manager.events.Close()
}

func (manager *Manager) runCourseTask(ctx context.Context, id string, request domain.CourseRequest) {
	defer manager.cancellations.Remove(id)
	logger.Infof("started course task id=%s", id)
	manager.mu.RLock()
	pipeline := manager.pipeline
	manager.mu.RUnlock()
	if pipeline == nil {
		manager.fail(id, fmt.Errorf("课程处理流水线尚未初始化"))
		return
	}
	manager.Update(id, domain.TaskUpdate{Status: domainStatus(domain.TaskRunning)})
	if err := pipeline.Run(ctx, request, func(update domain.TaskUpdate) { manager.Update(id, update) }); err != nil {
		manager.fail(id, err)
		return
	}
	manager.Update(id, domain.TaskUpdate{
		Status: domainStatus(domain.TaskCompleted), Progress: domain.Float(100),
		Message: domain.String("所有任务完成"), CurrentAction: domain.String("结束"), Speed: domain.Float(0),
	})
	logger.Infof("completed course task id=%s", id)
}

func (manager *Manager) runInstallTask(ctx context.Context, id, pluginID string) {
	defer manager.cancellations.Remove(id)
	logger.Infof("started plugin install task id=%s plugin=%s", id, pluginID)
	manager.Update(id, domain.TaskUpdate{
		Status: domainStatus(domain.TaskRunning), Progress: domain.Float(0), Message: domain.String("正在初始化..."),
	})
	if err := manager.installer.Install(ctx, pluginID, func(message string) {
		manager.Update(id, domain.TaskUpdate{Message: domain.String(message)})
	}); err != nil {
		manager.fail(id, err)
		return
	}
	manager.Update(id, domain.TaskUpdate{
		Status: domainStatus(domain.TaskCompleted), Progress: domain.Float(100),
		Message: domain.String("安装成功！"), Speed: domain.Float(0),
	})
	logger.Infof("completed plugin install task id=%s plugin=%s", id, pluginID)
}

func (manager *Manager) fail(id string, err error) {
	message := "任务执行失败"
	status := domain.TaskFailed
	if errors.Is(err, context.Canceled) {
		message = "任务已取消"
		status = domain.TaskCancelled
		logger.Warnf("cancelled task id=%s: %v", id, err)
	} else {
		logger.Errorf("failed task id=%s: %v", id, err)
	}
	manager.Update(id, domain.TaskUpdate{
		Status: domainStatus(status), Message: domain.String(message),
		Error: domain.String(err.Error()), Speed: domain.Float(0),
	})
}

func (manager *Manager) publish(item domain.Task) {
	manager.events.Publish(Event{Type: "task_update", Task: item.PublicView(time.Now(), manager.store.HasActive())})
}

func contains(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}

func domainStatus(status domain.TaskStatus) *domain.TaskStatus { return &status }

func startupTaskTitle(pluginID string) string {
	switch pluginID {
	case "whisper":
		return "Whisper 服务启动"
	case "funasr":
		return "FunASR 服务启动"
	case "slides_extractor":
		return "PPT 提取服务启动"
	default:
		return pluginID + " 服务启动"
	}
}
