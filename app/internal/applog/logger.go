package applog

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

const (
	defaultFileName = "smartclass_go.log"
	DefaultMaxBytes = 10 * 1024 * 1024
	DefaultBackups  = 5
)

type Level int

const (
	LevelDebug Level = iota
	LevelInfo
	LevelWarn
	LevelError
)

type Config struct {
	Dir      string
	FileName string
	Level    string
	Console  io.Writer
	MaxBytes int64
	Backups  int
	Now      func() time.Time
}

type Manager struct {
	mu      sync.Mutex
	file    *rotatingFileWriter
	console io.Writer
	level   Level
	now     func() time.Time
	closed  bool
}

type Logger struct {
	component string
}

var (
	globalMu sync.RWMutex
	fallback = &Manager{console: os.Stdout, level: LevelInfo, now: time.Now}
	current  = fallback
)

func Configure(config Config) (*Manager, error) {
	manager, err := New(config)
	if err != nil {
		return nil, err
	}
	globalMu.Lock()
	previous := current
	current = manager
	globalMu.Unlock()
	if previous != fallback {
		_ = previous.close()
	}
	return manager, nil
}

func New(config Config) (*Manager, error) {
	level, err := ParseLevel(config.Level)
	if err != nil {
		return nil, err
	}
	if config.Console == nil {
		config.Console = os.Stdout
	}
	if config.FileName == "" {
		config.FileName = defaultFileName
	}
	if filepath.Base(config.FileName) != config.FileName {
		return nil, fmt.Errorf("log filename must not contain a directory: %s", config.FileName)
	}
	if config.MaxBytes == 0 {
		config.MaxBytes = DefaultMaxBytes
	}
	if config.Backups == 0 {
		config.Backups = DefaultBackups
	}
	if config.Now == nil {
		config.Now = time.Now
	}
	file, err := newRotatingFileWriter(filepath.Join(config.Dir, config.FileName), config.MaxBytes, config.Backups)
	if err != nil {
		return nil, err
	}
	return &Manager{file: file, console: config.Console, level: level, now: config.Now}, nil
}

func ParseLevel(value string) (Level, error) {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "", "info":
		return LevelInfo, nil
	case "debug":
		return LevelDebug, nil
	case "warn", "warning":
		return LevelWarn, nil
	case "error":
		return LevelError, nil
	default:
		return LevelInfo, fmt.Errorf("unsupported log level %q; use debug, info, warn, or error", value)
	}
}

func Get(component string) *Logger {
	component = strings.TrimSpace(component)
	if component == "" {
		component = "smartclass"
	}
	return &Logger{component: component}
}

func (logger *Logger) Debugf(format string, args ...any) {
	logger.logf(LevelDebug, format, args...)
}

func (logger *Logger) Infof(format string, args ...any) {
	logger.logf(LevelInfo, format, args...)
}

func (logger *Logger) Warnf(format string, args ...any) {
	logger.logf(LevelWarn, format, args...)
}

func (logger *Logger) Errorf(format string, args ...any) {
	logger.logf(LevelError, format, args...)
}

func (logger *Logger) logf(level Level, format string, args ...any) {
	globalMu.RLock()
	manager := current
	globalMu.RUnlock()
	manager.log(level, logger.component, fmt.Sprintf(format, args...))
}

func (manager *Manager) log(level Level, component, message string) {
	if level < manager.level {
		return
	}
	message = strings.ReplaceAll(message, "\r\n", "\n")
	message = strings.ReplaceAll(message, "\r", "\n")
	message = strings.ReplaceAll(message, "\n", `\n`)
	line := fmt.Sprintf("[%s] %-7s [%s] %s\n", manager.now().Format("15:04:05"), level, component, message)

	manager.mu.Lock()
	defer manager.mu.Unlock()
	if manager.closed {
		return
	}
	if manager.console != nil {
		_, _ = io.WriteString(manager.console, line)
	}
	if manager.file != nil {
		_, _ = io.WriteString(manager.file, line)
	}
}

func (manager *Manager) Close() error {
	globalMu.Lock()
	if current == manager {
		current = fallback
	}
	globalMu.Unlock()
	return manager.close()
}

func (manager *Manager) close() error {
	manager.mu.Lock()
	defer manager.mu.Unlock()
	if manager.closed {
		return nil
	}
	manager.closed = true
	if manager.file == nil {
		return nil
	}
	return manager.file.Close()
}

func (level Level) String() string {
	switch level {
	case LevelDebug:
		return "DEBUG"
	case LevelInfo:
		return "INFO"
	case LevelWarn:
		return "WARN"
	case LevelError:
		return "ERROR"
	default:
		return "UNKNOWN"
	}
}
