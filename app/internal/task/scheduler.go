package task

import (
	"context"

	"smartclassdownloader/internal/config"
)

type Scheduler struct {
	downloadSlots chan struct{}
	slidesSlots   chan struct{}
	asrSlots      chan struct{}
}

func NewScheduler(settings config.Settings) *Scheduler {
	return &Scheduler{
		downloadSlots: make(chan struct{}, settings.MaxDownloadConcurrent),
		slidesSlots:   make(chan struct{}, 1),
		asrSlots:      make(chan struct{}, 1),
	}
}

func (scheduler *Scheduler) WithDownloadSlot(ctx context.Context, fn func() error) error {
	return withSlot(ctx, scheduler.downloadSlots, fn)
}

func (scheduler *Scheduler) WithSlidesSlot(ctx context.Context, fn func() error) error {
	return withSlot(ctx, scheduler.slidesSlots, fn)
}

func (scheduler *Scheduler) WithASRSlot(ctx context.Context, fn func() error) error {
	return withSlot(ctx, scheduler.asrSlots, fn)
}

func withSlot(ctx context.Context, slots chan struct{}, fn func() error) error {
	select {
	case slots <- struct{}{}:
		defer func() { <-slots }()
		return fn()
	case <-ctx.Done():
		return ctx.Err()
	}
}
