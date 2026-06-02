package aria2

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"sync/atomic"
	"time"
)

type RPCClient struct {
	URL       string
	Secret    string
	client    *http.Client
	requestID atomic.Uint64
}

type DownloadStatus struct {
	GID             string `json:"gid"`
	Status          string `json:"status"`
	TotalLength     string `json:"totalLength"`
	CompletedLength string `json:"completedLength"`
	DownloadSpeed   string `json:"downloadSpeed"`
	ErrorCode       string `json:"errorCode"`
	ErrorMessage    string `json:"errorMessage"`
}

func NewRPCClient(url, secret string, timeout time.Duration) *RPCClient {
	return &RPCClient{URL: url, Secret: secret, client: &http.Client{Timeout: timeout}}
}

func (client *RPCClient) Call(ctx context.Context, method string, params []any, out any) error {
	if client.Secret != "" {
		params = append([]any{"token:" + client.Secret}, params...)
	}
	payload := map[string]any{
		"jsonrpc": "2.0", "id": strconv.FormatUint(client.requestID.Add(1), 10),
		"method": method, "params": params,
	}
	body, _ := json.Marshal(payload)
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, client.URL, bytes.NewReader(body))
	if err != nil {
		return err
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := client.client.Do(request)
	if err != nil {
		return fmt.Errorf("aria2 RPC 请求失败: %w", err)
	}
	defer response.Body.Close()
	var result struct {
		Result json.RawMessage `json:"result"`
		Error  *struct {
			Code    int    `json:"code"`
			Message string `json:"message"`
		} `json:"error"`
	}
	if err := json.NewDecoder(response.Body).Decode(&result); err != nil {
		return err
	}
	if result.Error != nil {
		return fmt.Errorf("aria2 RPC %d: %s", result.Error.Code, result.Error.Message)
	}
	if out != nil {
		return json.Unmarshal(result.Result, out)
	}
	return nil
}

func (client *RPCClient) Version(ctx context.Context) error {
	var result map[string]any
	return client.Call(ctx, "aria2.getVersion", nil, &result)
}

func (client *RPCClient) AddURI(ctx context.Context, source string, options map[string]any) (string, error) {
	var gid string
	err := client.Call(ctx, "aria2.addUri", []any{[]string{source}, options}, &gid)
	return gid, err
}

func (client *RPCClient) TellStatus(ctx context.Context, gid string) (DownloadStatus, error) {
	var result DownloadStatus
	keys := []string{"gid", "status", "totalLength", "completedLength", "downloadSpeed", "errorCode", "errorMessage"}
	err := client.Call(ctx, "aria2.tellStatus", []any{gid, keys}, &result)
	return result, err
}

func (client *RPCClient) ForceRemove(ctx context.Context, gid string) error {
	var result string
	return client.Call(ctx, "aria2.forceRemove", []any{gid}, &result)
}

func (client *RPCClient) RemoveResult(ctx context.Context, gid string) error {
	var result string
	return client.Call(ctx, "aria2.removeDownloadResult", []any{gid}, &result)
}

func (client *RPCClient) Shutdown(ctx context.Context) error {
	var result string
	return client.Call(ctx, "aria2.shutdown", nil, &result)
}
