package web

import (
	"bytes"
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
