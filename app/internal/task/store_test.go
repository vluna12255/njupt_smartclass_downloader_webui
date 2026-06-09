package task

import (
	"testing"

	"smartclassdownloader/internal/domain"
)

func TestTerminalStateCannotRegress(t *testing.T) {
	store := NewStore()
	item := domain.NewTask("a", "title", domain.TaskKindCourse)
	item.Status = domain.TaskCompleted
	if err := store.Create(item); err != nil {
		t.Fatal(err)
	}
	running := domain.TaskRunning
	got, err := store.Update("a", domain.TaskUpdate{Status: &running})
	if err != nil {
		t.Fatal(err)
	}
	if got.Status != domain.TaskCompleted {
		t.Fatalf("terminal status regressed to %s", got.Status)
	}
}

func TestNextAvailableID(t *testing.T) {
	store := NewStore()
	_ = store.Create(domain.NewTask("video", "title", domain.TaskKindCourse))
	if got := store.NextAvailableID("video"); got != "video_1" {
		t.Fatalf("NextAvailableID() = %s", got)
	}
}

func TestTaskTitleCanBeUpdated(t *testing.T) {
	store := NewStore()
	_ = store.Create(domain.NewTask("video", "temporary title", domain.TaskKindCourse))

	got, err := store.Update("video", domain.TaskUpdate{Title: domain.String("resolved title")})
	if err != nil {
		t.Fatal(err)
	}
	if got.Title != "resolved title" {
		t.Fatalf("title = %q, want resolved title", got.Title)
	}
}
