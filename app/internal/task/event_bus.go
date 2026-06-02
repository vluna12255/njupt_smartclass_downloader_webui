package task

import (
	"sync"

	"smartclassdownloader/internal/domain"
)

type Event struct {
	Type string          `json:"type"`
	Task domain.TaskView `json:"data"`
}

type EventBus struct {
	mu          sync.RWMutex
	subscribers map[chan Event]struct{}
	closed      bool
}

func NewEventBus() *EventBus {
	return &EventBus{subscribers: map[chan Event]struct{}{}}
}

func (bus *EventBus) Subscribe(buffer int) (<-chan Event, func()) {
	channel := make(chan Event, buffer)
	bus.mu.Lock()
	if bus.closed {
		close(channel)
		bus.mu.Unlock()
		return channel, func() {}
	}
	bus.subscribers[channel] = struct{}{}
	bus.mu.Unlock()
	return channel, func() {
		bus.mu.Lock()
		if _, ok := bus.subscribers[channel]; ok {
			delete(bus.subscribers, channel)
			close(channel)
		}
		bus.mu.Unlock()
	}
}

func (bus *EventBus) Publish(event Event) {
	bus.mu.RLock()
	defer bus.mu.RUnlock()
	for subscriber := range bus.subscribers {
		select {
		case subscriber <- event:
		default:
		}
	}
}

func (bus *EventBus) Close() {
	bus.mu.Lock()
	defer bus.mu.Unlock()
	if bus.closed {
		return
	}
	bus.closed = true
	for subscriber := range bus.subscribers {
		close(subscriber)
	}
	bus.subscribers = nil
}
