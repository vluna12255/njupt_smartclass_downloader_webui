package plugin

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

type ServiceClient struct {
	client *http.Client
}

type ExtractSlidesRequest struct {
	VideoPath  string  `json:"video_path"`
	OutputPath string  `json:"output_path"`
	Threshold  float64 `json:"threshold"`
	MinTimeGap float64 `json:"min_time_gap"`
}

func NewServiceClient() *ServiceClient {
	return &ServiceClient{client: &http.Client{Timeout: time.Hour}}
}

func (client *ServiceClient) WaitHealthy(ctx context.Context, baseURL, healthPath string, interval time.Duration, startupError func() error) error {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		if startupError != nil {
			if err := startupError(); err != nil {
				return err
			}
		}
		request, _ := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimRight(baseURL, "/")+healthPath, nil)
		if response, err := client.client.Do(request); err == nil {
			_ = response.Body.Close()
			if response.StatusCode >= 200 && response.StatusCode < 500 {
				return nil
			}
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
	}
}

func (client *ServiceClient) Transcribe(ctx context.Context, engine, baseURL, audioPath, outputPath string) error {
	file, err := os.Open(audioPath)
	if err != nil {
		return err
	}
	defer file.Close()
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	part, err := writer.CreateFormFile("file", filepath.Base(audioPath))
	if err != nil {
		return err
	}
	if _, err := io.Copy(part, file); err != nil {
		return err
	}
	if engine == "whisper" {
		_ = writer.WriteField("config_json", whisperConfigJSON)
	}
	if err := writer.Close(); err != nil {
		return err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimRight(baseURL, "/")+"/transcribe", &body)
	if err != nil {
		return err
	}
	request.Header.Set("Content-Type", writer.FormDataContentType())
	response, err := client.client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	content, err := io.ReadAll(response.Body)
	if err != nil {
		return err
	}
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("字幕服务 HTTP %d: %s", response.StatusCode, strings.TrimSpace(string(content)))
	}
	if err := validateSRT(content); err != nil {
		return err
	}
	return os.WriteFile(outputPath, content, 0o644)
}

func (client *ServiceClient) ExtractSlides(ctx context.Context, baseURL string, payload ExtractSlidesRequest) error {
	body, _ := json.Marshal(payload)
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimRight(baseURL, "/")+"/extract_slides", bytes.NewReader(body))
	if err != nil {
		return err
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := client.client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		content, _ := io.ReadAll(response.Body)
		return fmt.Errorf("PPT 插件 HTTP %d: %s", response.StatusCode, strings.TrimSpace(string(content)))
	}
	return nil
}

func validateSRT(content []byte) error {
	if len(bytes.TrimSpace(content)) == 0 {
		return fmt.Errorf("字幕服务返回空内容")
	}
	lines := strings.Split(string(content), "\n")
	for index, line := range lines {
		if index >= 10 {
			break
		}
		if _, err := strconv.Atoi(strings.TrimSpace(line)); err == nil {
			return nil
		}
	}
	return fmt.Errorf("字幕服务返回内容不是有效 SRT")
}

const whisperConfigJSON = `{"whisper":{"model_size":"large-v3","lang":"chinese","is_translate":false,"beam_size":10,"compute_type":"float16","initial_prompt":"今天"},"vad":{"vad_filter":true,"threshold":0.3}}`
