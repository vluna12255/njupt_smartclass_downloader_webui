package task

import (
	"context"
	"errors"
	"testing"
	"time"

	"smartclassdownloader/internal/domain"
)

func TestAddCourseTaskPublishesQueuedTaskBeforeRunnerUpdates(t *testing.T) {
	root, cancel := context.WithCancel(context.Background())
	defer cancel()
	events := NewEventBus()
	manager := NewManager(root, NewStore(), events, nil)
	manager.SetPipeline(waitingCourseRunner{})
	channel, unsubscribe := events.Subscribe(4)
	defer unsubscribe()

	id, err := manager.AddCourseTask(context.Background(), domain.CourseRequest{VideoID: "video", Title: "selected title"})
	if err != nil {
		t.Fatal(err)
	}
	if id != "video" {
		t.Fatalf("id = %q, want video", id)
	}

	event := <-channel
	if event.Type != "task_update" {
		t.Fatalf("event type = %q, want task_update", event.Type)
	}
	if event.Task.Status != domain.TaskQueued {
		t.Fatalf("status = %q, want queued", event.Task.Status)
	}
	if event.Task.Title != "selected title" {
		t.Fatalf("title = %q, want selected title", event.Task.Title)
	}
}

type waitingCourseRunner struct{}

func (waitingCourseRunner) Run(ctx context.Context, _ domain.CourseRequest, _ func(domain.TaskUpdate)) error {
	<-ctx.Done()
	return ctx.Err()
}

func TestCourseRunnerFailureRemainsVisibleInTaskStore(t *testing.T) {
	manager := NewManager(context.Background(), NewStore(), NewEventBus(), nil)
	manager.SetPipeline(failingCourseRunner{})

	if _, err := manager.AddCourseTask(context.Background(), domain.CourseRequest{VideoID: "video", Title: "selected title"}); err != nil {
		t.Fatal(err)
	}

	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		views := manager.ListViews()
		if len(views) == 1 && views[0].Status == domain.TaskFailed {
			if views[0].Error != "video info failed" {
				t.Fatalf("error = %q, want video info failed", views[0].Error)
			}
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatalf("task did not remain visible as failed: %#v", manager.ListViews())
}

type failingCourseRunner struct{}

func (failingCourseRunner) Run(context.Context, domain.CourseRequest, func(domain.TaskUpdate)) error {
	return errors.New("video info failed")
}
