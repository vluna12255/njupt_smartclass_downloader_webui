package web

import (
	"bytes"
	"context"
	"errors"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"smartclassdownloader/internal/applog"
	"smartclassdownloader/internal/config"
	"smartclassdownloader/internal/domain"
	"smartclassdownloader/internal/platform"
	"smartclassdownloader/internal/plugin"
	taskservice "smartclassdownloader/internal/task"
)

func TestSearchKeywordParsesBrowserFormData(t *testing.T) {
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	if err := writer.WriteField("keyword", "  软件工程  "); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(http.MethodPost, "/search", &body)
	request.Header.Set("Content-Type", writer.FormDataContentType())

	if keyword := searchKeyword(request); keyword != "软件工程" {
		t.Fatalf("searchKeyword() = %q, want %q", keyword, "软件工程")
	}
}

func TestSearchKeywordParsesURLEncodedBody(t *testing.T) {
	body := url.Values{"keyword": {"  数据结构  "}}.Encode()
	request := httptest.NewRequest(http.MethodPost, "/search", strings.NewReader(body))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")

	if keyword := searchKeyword(request); keyword != "数据结构" {
		t.Fatalf("searchKeyword() = %q, want %q", keyword, "数据结构")
	}
}

func TestHTTPMiddlewareLogsRecoveredPanicAsInternalServerError(t *testing.T) {
	logDir := t.TempDir()
	manager, err := applog.Configure(applog.Config{Dir: logDir, Console: io.Discard})
	if err != nil {
		t.Fatal(err)
	}
	defer manager.Close()
	handler := requestLog(recoverPanic(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		panic("boom")
	})))
	response := httptest.NewRecorder()

	handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/panic", nil))

	if response.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d, want %d", response.Code, http.StatusInternalServerError)
	}
	body, err := os.ReadFile(filepath.Join(logDir, "smartclass_go.log"))
	if err != nil {
		t.Fatal(err)
	}
	logged := string(body)
	if !strings.Contains(logged, "[web] panic handling GET /panic: boom") {
		t.Fatalf("log does not contain panic: %q", logged)
	}
	if !strings.Contains(logged, "[web] GET /panic status=500") {
		t.Fatalf("log does not contain HTTP 500 request: %q", logged)
	}
}

func TestAddBatchCourseTasksQueuesSelectionsWithoutResolvingVideoInfo(t *testing.T) {
	var requests []domain.CourseRequest
	add := func(_ context.Context, request domain.CourseRequest) (string, error) {
		if request.VideoID == "bad" {
			return "", errors.New("queue rejected")
		}
		requests = append(requests, request)
		return request.VideoID, nil
	}
	base := domain.CourseRequest{TargetTypes: []string{"VGA"}, PluginDependencies: []string{"whisper"}}

	added, skipped := addBatchCourseTasks(
		context.Background(),
		add,
		[]string{" video-a ", "", "bad", "video-b"},
		[]string{" Selected title ", "unused", "bad title", ""},
		base,
	)

	if added != 2 || skipped != 2 {
		t.Fatalf("added/skipped = %d/%d, want 2/2", added, skipped)
	}
	if len(requests) != 2 {
		t.Fatalf("requests = %d, want 2", len(requests))
	}
	if requests[0].VideoID != "video-a" || requests[0].Title != "Selected title" {
		t.Fatalf("first request = %#v", requests[0])
	}
	if requests[1].VideoID != "video-b" || requests[1].Title != "视频任务 video-b" {
		t.Fatalf("second request = %#v", requests[1])
	}
	if len(requests[0].TargetTypes) != 1 || requests[0].TargetTypes[0] != "VGA" {
		t.Fatalf("base request fields were not preserved: %#v", requests[0])
	}
}

func TestBatchDownloadQueuesTaskWithoutResolvingVideoInfo(t *testing.T) {
	server := newBatchTestServer(t)
	request := batchDownloadRequest(t, map[string][]string{
		"video_ids":    {"video-a"},
		"video_titles": {"Selected title"},
		"file_types":   {"VGA"},
	})
	response := httptest.NewRecorder()

	server.batchDownload(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body=%s", response.Code, response.Body.String())
	}
	if !strings.Contains(response.Body.String(), `"added":1`) {
		t.Fatalf("response does not report queued task: %s", response.Body.String())
	}
	views := server.tasks.ListViews()
	if len(views) != 1 || views[0].Title != "Selected title" {
		t.Fatalf("queued tasks = %#v", views)
	}
}

func TestBatchDownloadRejectsWhenNoTaskCanBeQueued(t *testing.T) {
	server := newBatchTestServer(t)
	request := batchDownloadRequest(t, map[string][]string{"video_ids": {""}})
	response := httptest.NewRecorder()

	server.batchDownload(response, request)

	if response.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400; body=%s", response.Code, response.Body.String())
	}
	if strings.Contains(response.Body.String(), `"status":"success"`) {
		t.Fatalf("empty batch was reported as success: %s", response.Body.String())
	}
}

func newBatchTestServer(t *testing.T) *Server {
	t.Helper()
	ctx := context.Background()
	layout, err := platform.ResolveLayout(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if err := layout.EnsureMutableDirs(); err != nil {
		t.Fatal(err)
	}
	settings := config.NewService(layout, config.NewStore(layout), platform.NewCredentialStore())
	if err := settings.Load(ctx); err != nil {
		t.Fatal(err)
	}
	manifest, err := plugin.LoadManifest(filepath.Join(layout.PluginsDir, "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	registry, err := plugin.NewRegistry(manifest)
	if err != nil {
		t.Fatal(err)
	}
	plugins := plugin.NewManager(ctx, layout, registry, plugin.NewStatusStore(layout), plugin.NewInstaller(layout, registry), nil)
	tasks := taskservice.NewManager(ctx, taskservice.NewStore(), taskservice.NewEventBus(), plugins)
	return &Server{config: settings, plugins: plugins, tasks: tasks}
}

func batchDownloadRequest(t *testing.T, fields map[string][]string) *http.Request {
	t.Helper()
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	for key, values := range fields {
		for _, value := range values {
			if err := writer.WriteField(key, value); err != nil {
				t.Fatal(err)
			}
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(http.MethodPost, "/batch_download", &body)
	request.Header.Set("Content-Type", writer.FormDataContentType())
	return request
}
