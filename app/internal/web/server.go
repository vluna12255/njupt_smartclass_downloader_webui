package web

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"path/filepath"
	"runtime/debug"
	"strings"
	"time"

	"smartclassdownloader/internal/applog"
	"smartclassdownloader/internal/config"
	"smartclassdownloader/internal/domain"
	"smartclassdownloader/internal/platform"
	"smartclassdownloader/internal/plugin"
	"smartclassdownloader/internal/smartclass"
	"smartclassdownloader/internal/task"
)

type Server struct {
	layout   platform.Layout
	config   *config.Service
	sessions *smartclass.SessionManager
	tasks    *task.Manager
	plugins  *plugin.Manager
	renderer *Renderer
	hub      *Hub
	server   *http.Server
}

var logger = applog.Get("web")

func NewServer(layout platform.Layout, settings *config.Service, sessions *smartclass.SessionManager, tasks *task.Manager, plugins *plugin.Manager) (*Server, error) {
	renderer, err := NewRenderer()
	if err != nil {
		return nil, err
	}
	server := &Server{
		layout: layout, config: settings, sessions: sessions, tasks: tasks, plugins: plugins,
		renderer: renderer, hub: NewHub(tasks.Events()),
	}
	server.server = &http.Server{Handler: server.routes(), ReadHeaderTimeout: 10 * time.Second}
	return server, nil
}

func (server *Server) Start(ctx context.Context, host string, preferredPort int) (string, error) {
	listener, err := net.Listen("tcp", fmt.Sprintf("%s:%d", host, preferredPort))
	if err != nil {
		listener, err = net.Listen("tcp", net.JoinHostPort(host, "0"))
		if err != nil {
			return "", err
		}
	}
	go server.hub.Run(ctx)
	go func() {
		if err := server.server.Serve(listener); err != nil && err != http.ErrServerClosed {
			logger.Errorf("HTTP server stopped: %v", err)
		}
	}()
	return "http://" + listener.Addr().String(), nil
}

func (server *Server) Shutdown(ctx context.Context) error {
	server.hub.Close()
	return server.server.Shutdown(ctx)
}

func (server *Server) routes() http.Handler {
	mux := http.NewServeMux()
	mux.Handle("/static/", http.StripPrefix("/static/", http.FileServer(http.Dir(server.layout.StaticDir))))
	mux.HandleFunc("/", server.index)
	mux.HandleFunc("/login", server.login)
	mux.HandleFunc("/api/status", server.status)
	mux.HandleFunc("/search", server.search)
	mux.HandleFunc("/batch_download", server.batchDownload)
	mux.HandleFunc("/tasks_status", server.tasksStatus)
	mux.HandleFunc("/api/tasks", server.tasksJSON)
	mux.HandleFunc("/config", server.settings)
	mux.HandleFunc("/api/plugins/dependency_check", server.dependencyCheck)
	mux.HandleFunc("/api/plugins/", server.pluginRoute)
	mux.HandleFunc("/ws/tasks", server.taskWebSocket)
	return requestLog(recoverPanic(mux))
}

func (server *Server) index(w http.ResponseWriter, request *http.Request) {
	if request.URL.Path != "/" {
		http.NotFound(w, request)
		return
	}
	if _, err := server.sessions.Client(request.Context()); err != nil {
		server.renderer.Login(w, http.StatusOK, "")
		return
	}
	http.ServeFile(w, request, filepath.Join(server.layout.TemplatesDir, "index.html"))
}

func (server *Server) login(w http.ResponseWriter, request *http.Request) {
	if request.Method == http.MethodGet {
		server.renderer.Login(w, http.StatusOK, "")
		return
	}
	if request.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if err := request.ParseForm(); err != nil {
		server.renderer.Login(w, http.StatusBadRequest, "表单格式错误")
		return
	}
	username, password := request.FormValue("username"), request.FormValue("password")
	if err := server.sessions.Login(request.Context(), username, password); err != nil {
		server.renderer.Login(w, http.StatusUnauthorized, translateLoginError(err))
		return
	}
	if err := server.config.SaveCredentials(request.Context(), username, password); err != nil {
		server.renderer.Login(w, http.StatusInternalServerError, "凭证保存失败")
		return
	}
	http.Redirect(w, request, "/", http.StatusSeeOther)
}

func (server *Server) status(w http.ResponseWriter, request *http.Request) {
	JSON(w, http.StatusOK, map[string]any{
		"status": "online", "logged_in": server.sessions.IsValid(request.Context()),
		"websocket_connections": server.hub.Count(),
	})
}

func (server *Server) search(w http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	keyword := searchKeyword(request)
	if keyword == "" {
		server.renderer.VideoList(w, false, []domain.VideoSummary{})
		return
	}
	timeout := time.Duration(server.config.Current().NetworkTimeoutSeconds+5) * time.Second
	ctx, cancel := context.WithTimeout(request.Context(), timeout)
	defer cancel()
	client, err := server.sessions.ClientForOperation(ctx)
	if err != nil {
		searchError(w, http.StatusUnauthorized, "登录失效", "请检查账号密码配置或重新登录")
		return
	}
	videos := []domain.VideoSummary{}
	seenIDs := map[string]bool{}
	var searchErr error
searchVariants:
	for _, variant := range keywordVariants(keyword) {
		for page := 1; page <= 10; page++ {
			result, err := client.SearchVideos(ctx, domain.VideoSearchCondition{TitleKey: variant, PageNumber: page, PageSize: 50, Sort: "StartTime"})
			if err != nil {
				searchErr = err
				break searchVariants
			}
			for _, video := range result.Videos {
				if !seenIDs[video.ID] {
					seenIDs[video.ID] = true
					videos = append(videos, video)
				}
			}
			if len(result.Videos) < 50 || len(videos) >= 500 {
				break
			}
		}
	}
	if searchErr != nil && len(videos) == 0 {
		if ctx.Err() != nil {
			searchError(w, http.StatusGatewayTimeout, "搜索超时", "SmartClass 响应较慢，请稍后重试")
			return
		}
		searchError(w, http.StatusBadGateway, "搜索失败", "SmartClass 暂时无法返回课程，请稍后重试")
		return
	}
	server.renderer.VideoList(w, true, videos)
}

func (server *Server) batchDownload(w http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if err := request.ParseMultipartForm(4 << 20); err != nil {
		ErrorJSON(w, http.StatusBadRequest, "提交内容格式错误")
		return
	}
	videoIDs := request.MultipartForm.Value["video_ids"]
	videoTitles := request.MultipartForm.Value["video_titles"]
	fileTypes := request.MultipartForm.Value["file_types"]
	if len(videoIDs) == 0 {
		ErrorJSON(w, http.StatusBadRequest, "未选择任何视频")
		return
	}
	if contains(fileTypes, "PPT") && !server.plugins.IsInstalled("slides_extractor") {
		fileTypes = without(fileTypes, "PPT")
	}
	settings := server.config.Current()
	engine := request.FormValue("asr_engine")
	if engine == "" {
		engine = settings.ASREngine
	}
	transcribe := []string{}
	if server.plugins.IsInstalled(engine) {
		for _, value := range []struct{ key, track string }{{"whisper_vga", "VGA"}, {"whisper_video1", "Video1"}, {"whisper_video2", "Video2"}} {
			if request.FormValue(value.key) == "true" {
				transcribe = append(transcribe, value.track)
			}
		}
	}
	dependencies := []string{}
	if contains(fileTypes, "PPT") {
		dependencies = append(dependencies, "slides_extractor")
	}
	if len(transcribe) > 0 {
		dependencies = append(dependencies, engine)
	}
	url := settings.ASRServiceURL()
	if engine == "funasr" {
		url = strings.Replace(url, ":8000", ":8001", 1)
	} else {
		url = strings.Replace(url, ":8001", ":8000", 1)
	}
	added, skipped := addBatchCourseTasks(request.Context(), server.tasks.AddCourseTask, videoIDs, videoTitles, domain.CourseRequest{
		TargetTypes: fileTypes, TranscribeTargets: transcribe, ASRServiceURL: url, PluginDependencies: dependencies,
	})
	if added == 0 {
		ErrorJSON(w, http.StatusBadRequest, "未能添加任何任务")
		return
	}
	message := fmt.Sprintf("已添加 %d 个任务", added)
	if skipped > 0 {
		message += fmt.Sprintf("，跳过 %d 个", skipped)
	}
	JSON(w, http.StatusOK, map[string]any{"status": "success", "msg": message, "added": added, "skipped": skipped})
}

func addBatchCourseTasks(
	ctx context.Context,
	add func(context.Context, domain.CourseRequest) (string, error),
	videoIDs, videoTitles []string,
	base domain.CourseRequest,
) (added, skipped int) {
	for index, value := range videoIDs {
		videoID := strings.TrimSpace(value)
		if videoID == "" {
			skipped++
			continue
		}
		item := base
		item.VideoID = videoID
		item.Title = batchCourseTitle(videoID, videoTitles, index)
		if _, err := add(ctx, item); err != nil {
			logger.Warnf("queue course task video_id=%s: %v", videoID, err)
			skipped++
			continue
		}
		added++
	}
	return added, skipped
}

func batchCourseTitle(videoID string, titles []string, index int) string {
	if index < len(titles) {
		if title := strings.TrimSpace(titles[index]); title != "" {
			return title
		}
	}
	return "视频任务 " + videoID
}

func (server *Server) tasksStatus(w http.ResponseWriter, _ *http.Request) {
	server.renderer.TaskList(w, server.tasks.ListViews())
}

func (server *Server) tasksJSON(w http.ResponseWriter, _ *http.Request) {
	JSON(w, http.StatusOK, server.tasks.ListViews())
}

func (server *Server) settings(w http.ResponseWriter, request *http.Request) {
	if request.Method == http.MethodGet {
		JSON(w, http.StatusOK, server.config.PublicView(request.Context()))
		return
	}
	if request.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var patch map[string]any
	if err := decodeJSON(request, &patch); err != nil {
		ErrorJSON(w, http.StatusBadRequest, "配置格式错误")
		return
	}
	reconcilePluginSettings(patch, server.plugins)
	if _, err := server.config.Save(request.Context(), patch); err != nil {
		ErrorJSON(w, http.StatusBadRequest, err.Error())
		return
	}
	JSON(w, http.StatusOK, map[string]any{"status": "success", "msg": "设置已保存"})
}

func (server *Server) dependencyCheck(w http.ResponseWriter, _ *http.Request) {
	JSON(w, http.StatusOK, map[string]bool{
		"whisper": server.plugins.IsInstalled("whisper"), "funasr": server.plugins.IsInstalled("funasr"),
		"slides_extractor": server.plugins.IsInstalled("slides_extractor"),
	})
}

func (server *Server) pluginRoute(w http.ResponseWriter, request *http.Request) {
	parts := strings.Split(strings.TrimPrefix(request.URL.Path, "/api/plugins/"), "/")
	if len(parts) != 2 {
		http.NotFound(w, request)
		return
	}
	id, action := parts[0], parts[1]
	if _, ok := server.plugins.Registry().Get(id); !ok {
		ErrorJSON(w, http.StatusBadRequest, "未知插件")
		return
	}
	switch action {
	case "status":
		status := server.plugins.Status(request.Context(), id, false)
		status.Installing = hasRunningTask(server.tasks.ListViews(), "install_"+id)
		JSON(w, http.StatusOK, map[string]any{
			"installed": status.Installed, "running": status.Running, "installing": status.Installing,
			"uninstalling": status.Uninstalling, "model_status": status.ModelStatus,
			"model_downloading": status.ModelStatus.Phase == "downloading", "model_progress": status.ModelStatus.Progress,
			"model_speed": status.ModelStatus.Speed,
		})
	case "install":
		if request.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		if _, err := server.tasks.AddPluginInstallTask(request.Context(), id); err != nil {
			ErrorJSON(w, http.StatusBadRequest, err.Error())
			return
		}
		JSON(w, http.StatusOK, map[string]any{"status": "success"})
	case "uninstall":
		if request.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		server.tasks.AbortPluginTasks(id)
		if err := server.plugins.Uninstall(request.Context(), id); err != nil {
			ErrorJSON(w, http.StatusInternalServerError, err.Error())
			return
		}
		JSON(w, http.StatusOK, map[string]any{"status": "success", "msg": "卸载成功"})
	case "startup_report":
		if request.Method != http.MethodPost || !isLoopback(request.RemoteAddr) {
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
		body, err := plugin.DecodeReport(request)
		if err != nil || server.plugins.AcceptReport(id, body) != nil {
			ErrorJSON(w, http.StatusBadRequest, "状态回报格式错误")
			return
		}
		JSON(w, http.StatusOK, map[string]any{"status": "ok"})
	default:
		http.NotFound(w, request)
	}
}

func (server *Server) taskWebSocket(w http.ResponseWriter, request *http.Request) {
	client, err := upgradeWebSocket(w, request)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	server.hub.add(client)
	defer server.hub.remove(client)
	initial, _ := jsonMarshal(map[string]any{"type": "task_list", "data": server.tasks.ListViews()})
	client.queue(initial)
	go client.writeLoop()
	_ = client.readLoop(func(message string) {
		if message == "ping" {
			client.queue([]byte("pong"))
		}
	})
}

func requestLog(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		start := time.Now()
		recorder := &responseRecorder{ResponseWriter: w}
		next.ServeHTTP(recorder, request)
		status := recorder.status
		if status == 0 {
			status = http.StatusOK
		}
		message := fmt.Sprintf("%s %s status=%d duration=%s", request.Method, request.URL.Path, status, time.Since(start).Round(time.Millisecond))
		switch {
		case status >= http.StatusInternalServerError:
			logger.Errorf("%s", message)
		case status >= http.StatusBadRequest:
			logger.Warnf("%s", message)
		default:
			logger.Infof("%s", message)
		}
	})
}

func recoverPanic(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		defer func() {
			if value := recover(); value != nil {
				logger.Errorf("panic handling %s %s: %v\n%s", request.Method, request.URL.Path, value, debug.Stack())
				http.Error(w, "internal server error", http.StatusInternalServerError)
			}
		}()
		next.ServeHTTP(w, request)
	})
}

type responseRecorder struct {
	http.ResponseWriter
	status int
}

func (recorder *responseRecorder) WriteHeader(status int) {
	if recorder.status != 0 {
		return
	}
	recorder.status = status
	recorder.ResponseWriter.WriteHeader(status)
}

func (recorder *responseRecorder) Write(body []byte) (int, error) {
	if recorder.status == 0 {
		recorder.WriteHeader(http.StatusOK)
	}
	return recorder.ResponseWriter.Write(body)
}

func (recorder *responseRecorder) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	hijacker, ok := recorder.ResponseWriter.(http.Hijacker)
	if !ok {
		return nil, nil, fmt.Errorf("response writer cannot hijack")
	}
	if recorder.status == 0 {
		recorder.status = http.StatusSwitchingProtocols
	}
	return hijacker.Hijack()
}

func (recorder *responseRecorder) Flush() {
	if recorder.status == 0 {
		recorder.WriteHeader(http.StatusOK)
	}
	if flusher, ok := recorder.ResponseWriter.(http.Flusher); ok {
		flusher.Flush()
	}
}

func (recorder *responseRecorder) Unwrap() http.ResponseWriter {
	return recorder.ResponseWriter
}

func translateLoginError(err error) string {
	value := strings.ToLower(err.Error())
	switch {
	case strings.Contains(value, "timeout"):
		return "网络连接超时，请检查网络或稍后重试"
	case strings.Contains(value, "connection"):
		return "网络连接错误，请检查网络"
	default:
		return "账号或密码错误"
	}
}

func keywordVariants(keyword string) []string {
	values := []string{keyword, strings.ToLower(keyword), strings.ToUpper(keyword)}
	seen := map[string]bool{}
	var result []string
	for _, value := range values {
		if !seen[value] {
			seen[value] = true
			result = append(result, value)
		}
	}
	return result
}

func reconcilePluginSettings(patch map[string]any, plugins *plugin.Manager) {
	engine, _ := patch["asr_engine"].(string)
	if engine == "whisper" && !plugins.IsInstalled("whisper") && plugins.IsInstalled("funasr") {
		patch["asr_engine"] = "funasr"
	}
	if engine == "funasr" && !plugins.IsInstalled("funasr") && plugins.IsInstalled("whisper") {
		patch["asr_engine"] = "whisper"
	}
	if !plugins.IsInstalled("whisper") && !plugins.IsInstalled("funasr") {
		patch["default_whisper_vga"], patch["default_whisper_video1"], patch["default_whisper_video2"] = false, false, false
		patch["auto_whisper"] = false
	}
	if !plugins.IsInstalled("slides_extractor") {
		patch["default_ppt"] = false
	}
}

func searchError(w http.ResponseWriter, status int, title, detail string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(status)
	_, _ = fmt.Fprintf(w, `<div class="col-span-full text-center text-red-500 py-20"><p class="text-base font-medium">%s</p><p class="text-sm mt-2">%s</p></div>`, title, detail)
}

func searchKeyword(request *http.Request) string {
	// FormValue parses both browser FormData multipart bodies and classic
	// application/x-www-form-urlencoded bodies.
	return strings.TrimSpace(request.FormValue("keyword"))
}

func contains(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}

func without(values []string, target string) []string {
	var result []string
	for _, value := range values {
		if value != target {
			result = append(result, value)
		}
	}
	return result
}

func hasRunningTask(values []domain.TaskView, id string) bool {
	for _, task := range values {
		if task.ID == id && (task.Status == domain.TaskQueued || task.Status == domain.TaskRunning || task.Status == domain.TaskWaiting) {
			return true
		}
	}
	return false
}

func isLoopback(remote string) bool {
	host, _, err := net.SplitHostPort(remote)
	if err != nil {
		return false
	}
	address := net.ParseIP(host)
	return address != nil && address.IsLoopback()
}

func decodeJSON(request *http.Request, out any) error {
	defer request.Body.Close()
	return jsonNewDecoder(request).Decode(out)
}

func jsonMarshal(value any) ([]byte, error) {
	return json.Marshal(value)
}

func jsonNewDecoder(request *http.Request) *json.Decoder {
	return json.NewDecoder(request.Body)
}
