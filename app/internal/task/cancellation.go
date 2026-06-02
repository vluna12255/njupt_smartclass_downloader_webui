package task

import (
	"context"
	"sync"
)

type CancellationRegistry struct {
	mu      sync.Mutex
	cancels map[string]context.CancelFunc
}

func NewCancellationRegistry() *CancellationRegistry {
	return &CancellationRegistry{cancels: map[string]context.CancelFunc{}}
}

func (registry *CancellationRegistry) Register(taskID string, cancel context.CancelFunc) {
	registry.mu.Lock()
	registry.cancels[taskID] = cancel
	registry.mu.Unlock()
}

func (registry *CancellationRegistry) Cancel(taskID string) bool {
	registry.mu.Lock()
	cancel := registry.cancels[taskID]
	delete(registry.cancels, taskID)
	registry.mu.Unlock()
	if cancel == nil {
		return false
	}
	cancel()
	return true
}

func (registry *CancellationRegistry) Remove(taskID string) {
	registry.mu.Lock()
	delete(registry.cancels, taskID)
	registry.mu.Unlock()
}

func (registry *CancellationRegistry) CancelAll() {
	registry.mu.Lock()
	values := make([]context.CancelFunc, 0, len(registry.cancels))
	for _, cancel := range registry.cancels {
		values = append(values, cancel)
	}
	registry.cancels = map[string]context.CancelFunc{}
	registry.mu.Unlock()
	for _, cancel := range values {
		cancel()
	}
}
