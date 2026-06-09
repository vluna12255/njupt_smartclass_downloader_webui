package task

import (
	"context"
	"sync"
	"testing"
	"time"

	"smartclassdownloader/internal/config"
)

func TestPrepareSlotsLimitConcurrentCourseParsing(t *testing.T) {
	scheduler := NewScheduler(config.Settings{MaxDownloadConcurrent: 2})
	entered := make(chan struct{}, 3)
	release := make(chan struct{}, 3)
	var group sync.WaitGroup

	for index := 0; index < 3; index++ {
		group.Add(1)
		go func() {
			defer group.Done()
			if err := scheduler.WithPrepareSlot(context.Background(), func() error {
				entered <- struct{}{}
				<-release
				return nil
			}); err != nil {
				t.Errorf("WithPrepareSlot() error = %v", err)
			}
		}()
	}

	waitForSignal(t, entered)
	waitForSignal(t, entered)
	select {
	case <-entered:
		t.Fatal("third prepare task entered before a slot was released")
	case <-time.After(50 * time.Millisecond):
	}

	release <- struct{}{}
	waitForSignal(t, entered)
	release <- struct{}{}
	release <- struct{}{}
	group.Wait()
}

func waitForSignal(t *testing.T, channel <-chan struct{}) {
	t.Helper()
	select {
	case <-channel:
	case <-time.After(time.Second):
		t.Fatal("timed out waiting for slot")
	}
}
