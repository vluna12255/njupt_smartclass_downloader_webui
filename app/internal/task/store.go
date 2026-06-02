package task

import (
	"fmt"
	"sort"
	"sync"
	"time"

	"smartclassdownloader/internal/domain"
)

type Store struct {
	mu    sync.RWMutex
	tasks map[string]domain.Task
}

func NewStore() *Store {
	return &Store{tasks: map[string]domain.Task{}}
}

func (store *Store) Create(task domain.Task) error {
	store.mu.Lock()
	defer store.mu.Unlock()
	if _, exists := store.tasks[task.ID]; exists {
		return fmt.Errorf("task already exists: %s", task.ID)
	}
	store.tasks[task.ID] = task
	return nil
}

func (store *Store) Replace(task domain.Task) {
	store.mu.Lock()
	store.tasks[task.ID] = task
	store.mu.Unlock()
}

func (store *Store) NextAvailableID(base string) string {
	store.mu.RLock()
	defer store.mu.RUnlock()
	if _, exists := store.tasks[base]; !exists {
		return base
	}
	for suffix := 1; ; suffix++ {
		candidate := fmt.Sprintf("%s_%d", base, suffix)
		if _, exists := store.tasks[candidate]; !exists {
			return candidate
		}
	}
}

func (store *Store) Get(id string) (domain.Task, bool) {
	store.mu.RLock()
	defer store.mu.RUnlock()
	task, ok := store.tasks[id]
	return task, ok
}

func (store *Store) Update(id string, update domain.TaskUpdate) (domain.Task, error) {
	store.mu.Lock()
	defer store.mu.Unlock()
	task, ok := store.tasks[id]
	if !ok {
		return domain.Task{}, fmt.Errorf("task not found: %s", id)
	}
	if task.IsTerminal() && update.Status != nil {
		next := *update.Status
		if next != domain.TaskCompleted && next != domain.TaskFailed && next != domain.TaskCancelled {
			return task, nil
		}
	} else if task.IsTerminal() {
		return task, nil
	}
	task.Apply(update)
	store.tasks[id] = task
	return task, nil
}

func (store *Store) List() []domain.Task {
	store.mu.RLock()
	defer store.mu.RUnlock()
	result := make([]domain.Task, 0, len(store.tasks))
	for _, task := range store.tasks {
		result = append(result, task)
	}
	sort.SliceStable(result, func(i, j int) bool {
		return result[i].CreatedAt.Before(result[j].CreatedAt)
	})
	return result
}

func (store *Store) HasActive() bool {
	store.mu.RLock()
	defer store.mu.RUnlock()
	for _, task := range store.tasks {
		if task.Status == domain.TaskQueued || task.Status == domain.TaskRunning || task.Status == domain.TaskWaiting {
			return true
		}
	}
	return false
}

func (store *Store) Views() []domain.TaskView {
	now := time.Now()
	active := store.HasActive()
	tasks := store.List()
	result := make([]domain.TaskView, 0, len(tasks))
	for _, item := range tasks {
		result = append(result, item.PublicView(now, active))
	}
	return result
}
