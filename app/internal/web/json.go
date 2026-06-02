package web

import (
	"encoding/json"
	"net/http"
)

func JSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func ErrorJSON(w http.ResponseWriter, status int, message string) {
	JSON(w, status, map[string]any{"status": "error", "msg": message})
}
