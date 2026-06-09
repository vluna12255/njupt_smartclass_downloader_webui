package domain

import (
	"time"

	display "smartclassdownloader/internal/format"
)

type TaskStatus string
type TaskKind string

const (
	TaskQueued    TaskStatus = "queued"
	TaskRunning   TaskStatus = "running"
	TaskCompleted TaskStatus = "completed"
	TaskFailed    TaskStatus = "failed"
	TaskWaiting   TaskStatus = "waiting"
	TaskCancelled TaskStatus = "cancelled"

	TaskKindCourse        TaskKind = "course"
	TaskKindPluginInstall TaskKind = "plugin_install"
	TaskKindPluginStartup TaskKind = "plugin_startup"
)

type Task struct {
	ID                 string
	Kind               TaskKind
	Title              string
	Status             TaskStatus
	Progress           float64
	TotalSize          int64
	DownloadedSize     int64
	Speed              float64
	CurrentAction      string
	Message            string
	Error              string
	PluginDependencies []string
	CreatedAt          time.Time
	UpdatedAt          time.Time
}

type TaskUpdate struct {
	Title          *string
	Status         *TaskStatus
	Progress       *float64
	TotalSize      *int64
	DownloadedSize *int64
	Speed          *float64
	CurrentAction  *string
	Message        *string
	Error          *string
}

type TaskView struct {
	ID             string     `json:"id"`
	Title          string     `json:"title"`
	Status         TaskStatus `json:"status"`
	StatusText     string     `json:"status_text"`
	Progress       float64    `json:"progress"`
	TotalSize      int64      `json:"total_size"`
	TotalSizeStr   string     `json:"total_size_str"`
	DownloadedSize int64      `json:"downloaded_size"`
	DownloadedStr  string     `json:"downloaded_str"`
	Speed          float64    `json:"speed"`
	SpeedStr       string     `json:"speed_str"`
	CurrentAction  string     `json:"current_action"`
	Message        string     `json:"message"`
	Error          string     `json:"error"`
	ETAStr         string     `json:"eta_str"`
	DurationStr    string     `json:"duration_str"`
	CreatedAt      string     `json:"created_at"`
	UpdatedAt      string     `json:"updated_at"`
	HasActiveTasks bool       `json:"has_active_tasks"`
}

func NewTask(id, title string, kind TaskKind) Task {
	now := time.Now()
	return Task{
		ID: id, Kind: kind, Title: title, Status: TaskQueued,
		Message: "等待中...", CreatedAt: now, UpdatedAt: now,
	}
}

func (task *Task) Apply(update TaskUpdate) {
	if update.Title != nil {
		task.Title = *update.Title
	}
	if update.Status != nil {
		task.Status = *update.Status
	}
	if update.Progress != nil {
		task.Progress = *update.Progress
	}
	if update.TotalSize != nil {
		task.TotalSize = *update.TotalSize
	}
	if update.DownloadedSize != nil {
		task.DownloadedSize = *update.DownloadedSize
	}
	if update.Speed != nil {
		task.Speed = *update.Speed
	}
	if update.CurrentAction != nil {
		task.CurrentAction = *update.CurrentAction
	}
	if update.Message != nil {
		task.Message = *update.Message
	}
	if update.Error != nil {
		task.Error = *update.Error
	}
	task.UpdatedAt = time.Now()
}

func (task Task) IsTerminal() bool {
	return task.Status == TaskCompleted || task.Status == TaskFailed || task.Status == TaskCancelled
}

func (task Task) StatusText() string {
	return map[TaskStatus]string{
		TaskQueued: "排队中", TaskRunning: "进行中", TaskWaiting: "等待资源",
		TaskCompleted: "已完成", TaskFailed: "失败", TaskCancelled: "已取消",
	}[task.Status]
}

func (task Task) PublicView(now time.Time, hasActive bool) TaskView {
	return TaskView{
		ID: task.ID, Title: task.Title, Status: task.Status, StatusText: task.StatusText(),
		Progress: task.Progress, TotalSize: task.TotalSize, TotalSizeStr: display.Size(float64(task.TotalSize), 1),
		DownloadedSize: task.DownloadedSize, DownloadedStr: display.Size(float64(task.DownloadedSize), 1),
		Speed: task.Speed, SpeedStr: display.Speed(task.Speed), CurrentAction: task.CurrentAction,
		Message: task.Message, Error: task.Error, ETAStr: display.ETA(task.TotalSize, task.DownloadedSize, task.Speed),
		DurationStr: display.Duration(int64(now.Sub(task.CreatedAt).Seconds())),
		CreatedAt:   task.CreatedAt.Format(time.RFC3339), UpdatedAt: task.UpdatedAt.Format(time.RFC3339),
		HasActiveTasks: hasActive,
	}
}

func StatusUpdate(status TaskStatus) TaskUpdate { return TaskUpdate{Status: &status} }
func String(value string) *string               { return &value }
func Float(value float64) *float64              { return &value }
func Int64(value int64) *int64                  { return &value }
