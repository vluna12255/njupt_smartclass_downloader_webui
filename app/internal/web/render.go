package web

import (
	_ "embed"
	"html/template"
	"net/http"
)

//go:embed templates/login.html
var loginTemplate string

//go:embed templates/video_list.html
var videoListTemplate string

//go:embed templates/task_list.html
var taskListTemplate string

type Renderer struct {
	login     *template.Template
	videoList *template.Template
	taskList  *template.Template
}

func NewRenderer() (*Renderer, error) {
	login, err := template.New("login").Parse(loginTemplate)
	if err != nil {
		return nil, err
	}
	videoList, err := template.New("video_list").Parse(videoListTemplate)
	if err != nil {
		return nil, err
	}
	taskList, err := template.New("task_list").Parse(taskListTemplate)
	if err != nil {
		return nil, err
	}
	return &Renderer{login: login, videoList: videoList, taskList: taskList}, nil
}

func (renderer *Renderer) Login(w http.ResponseWriter, status int, errorMessage string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(status)
	_ = renderer.login.Execute(w, map[string]any{"Error": errorMessage})
}

func (renderer *Renderer) VideoList(w http.ResponseWriter, started bool, videos any) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_ = renderer.videoList.Execute(w, map[string]any{"Started": started, "Videos": videos})
}

func (renderer *Renderer) TaskList(w http.ResponseWriter, tasks any) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_ = renderer.taskList.Execute(w, tasks)
}
