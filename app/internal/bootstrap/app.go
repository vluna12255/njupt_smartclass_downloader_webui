package bootstrap

import (
	"context"
	"fmt"
	"path/filepath"
	"time"

	"smartclassdownloader/internal/applog"
	"smartclassdownloader/internal/aria2"
	"smartclassdownloader/internal/cleanup"
	"smartclassdownloader/internal/config"
	"smartclassdownloader/internal/media"
	"smartclassdownloader/internal/platform"
	"smartclassdownloader/internal/plugin"
	"smartclassdownloader/internal/smartclass"
	"smartclassdownloader/internal/task"
	"smartclassdownloader/internal/web"
	"smartclassdownloader/internal/workflow"
)

type Options struct {
	RootDir     string
	Port        int
	OpenBrowser bool
	LogLevel    string
}

type Application struct {
	Layout    platform.Layout
	Config    *config.Service
	Sessions  *smartclass.SessionManager
	Tasks     *task.Manager
	Plugins   *plugin.Manager
	Downloads *aria2.DownloadService
	Workflows *workflow.Pipeline
	Web       *web.Server
	Cleanup   *cleanup.Service
	aria2     *aria2.ProcessManager
	browser   platform.Browser
	logs      *applog.Manager
	options   Options
	baseURL   string
	cleanup   cleanup.Summary
}

var logger = applog.Get("bootstrap")

func NewApplication(ctx context.Context, options Options) (_ *Application, err error) {
	if options.Port == 0 {
		options.Port = 8080
	}
	layout, err := platform.ResolveLayout(options.RootDir)
	if err != nil {
		return nil, err
	}
	if err := layout.EnsureMutableDirs(); err != nil {
		return nil, err
	}
	logsRemoved, logsErr := cleanup.CleanLogs(layout.LogsDir)
	logs, err := applog.Configure(applog.Config{Dir: layout.LogsDir, Level: options.LogLevel})
	if err != nil {
		return nil, fmt.Errorf("configure logging: %w", err)
	}
	succeeded := false
	defer func() {
		if !succeeded {
			if err != nil {
				logger.Errorf("initialize application: %v", err)
			}
			_ = logs.Close()
		}
	}()
	logger.Infof("initializing application root=%s", layout.RootDir)
	settings := config.NewService(layout, config.NewStore(layout), platform.NewCredentialStore())
	if err := settings.Load(ctx); err != nil {
		return nil, fmt.Errorf("load settings: %w", err)
	}
	manifest, err := plugin.LoadManifest(filepath.Join(layout.PluginsDir, "manifest.json"))
	if err != nil {
		return nil, fmt.Errorf("load plugin manifest: %w", err)
	}
	registry, err := plugin.NewRegistry(manifest)
	if err != nil {
		return nil, err
	}
	ariaBinary := aria2.NewBinaryManager(layout, settings)
	ariaProcess := aria2.NewProcessManager(ariaBinary)
	downloads := aria2.NewDownloadService(ariaProcess, settings)
	statusStore := plugin.NewStatusStore(layout)
	installer := plugin.NewInstaller(layout, registry)
	plugins := plugin.NewManager(ctx, layout, registry, statusStore, installer, ariaProcess)
	sessions := smartclass.NewSessionManager(settings)
	store := task.NewStore()
	events := task.NewEventBus()
	tasks := task.NewManager(ctx, store, events, plugins)
	scheduler := task.NewScheduler(settings.Current())
	serviceClient := plugin.NewServiceClient()
	ffmpeg := media.NewFFmpeg(layout)
	pipeline := workflow.NewPipeline(
		workflow.NewDownloadStep(sessions, downloads, scheduler, settings),
		workflow.NewSlidesStep(plugins, serviceClient, scheduler),
		workflow.NewTranscribeStep(plugins, serviceClient, ffmpeg, scheduler),
	)
	tasks.SetPipeline(pipeline)
	plugins.SetCallbacks(tasks.Update, tasks.EnsurePluginStartupTask)
	webServer, err := web.NewServer(layout, settings, sessions, tasks, plugins)
	if err != nil {
		return nil, err
	}
	application := &Application{
		Layout: layout, Config: settings, Sessions: sessions, Tasks: tasks, Plugins: plugins,
		Downloads: downloads, Workflows: pipeline, Web: webServer, Cleanup: cleanup.NewService(layout, settings),
		aria2: ariaProcess, browser: platform.NewBrowser(), logs: logs, options: options,
		cleanup: cleanup.Summary{LogsRemoved: logsRemoved, LogsError: logsErr},
	}
	succeeded = true
	return application, nil
}

func (application *Application) Validate() error {
	for _, path := range []string{application.Layout.TemplatesDir, application.Layout.StaticDir, application.Layout.PluginsDir} {
		if path == "" {
			return fmt.Errorf("application layout is incomplete")
		}
	}
	return nil
}

func (application *Application) Start(ctx context.Context) error {
	if err := application.Validate(); err != nil {
		logger.Errorf("validate application: %v", err)
		return err
	}
	summary := application.cleanup
	summary.TemporaryRemoved, summary.TemporaryError = application.Cleanup.CleanTemporaryDownloads()
	logger.Infof("startup cleanup: %d logs, %d temporary files", summary.LogsRemoved, summary.TemporaryRemoved)
	if summary.LogsError != nil {
		logger.Warnf("clean old logs: %v", summary.LogsError)
	}
	if summary.TemporaryError != nil {
		logger.Warnf("clean temporary files: %v", summary.TemporaryError)
	}
	baseURL, err := application.Web.Start(ctx, "127.0.0.1", application.options.Port)
	if err != nil {
		logger.Errorf("start HTTP server: %v", err)
		return err
	}
	application.baseURL = baseURL
	application.Plugins.SetMainServerURL(baseURL)
	if application.Config.Current().AutoLogin {
		go func() {
			autoCtx, cancel := context.WithTimeout(ctx, 45*time.Second)
			defer cancel()
			if err := application.Sessions.AutoLogin(autoCtx); err != nil {
				logger.Warnf("auto login skipped: %v", err)
			}
		}()
	}
	if application.options.OpenBrowser {
		go func() {
			time.Sleep(250 * time.Millisecond)
			if err := application.browser.Open(baseURL); err != nil {
				logger.Warnf("open browser: %v", err)
			}
		}()
	}
	logger.Infof("SmartClass Downloader Go host listening on %s", baseURL)
	return nil
}

func (application *Application) Shutdown(ctx context.Context) error {
	logger.Infof("shutting down application")
	defer application.CloseLogs()
	var lastErr error
	if err := application.Web.Shutdown(ctx); err != nil {
		logger.Errorf("shutdown HTTP server: %v", err)
		lastErr = err
	}
	application.Tasks.CancelAll()
	if err := application.Plugins.StopAll(ctx); err != nil {
		logger.Errorf("stop plugins: %v", err)
		lastErr = err
	}
	if err := application.aria2.Stop(ctx); err != nil {
		logger.Errorf("stop aria2: %v", err)
		lastErr = err
	}
	application.Tasks.Close()
	logger.Infof("application shutdown completed")
	return lastErr
}

func (application *Application) BaseURL() string { return application.baseURL }

func (application *Application) CloseLogs() {
	if application.logs != nil {
		_ = application.logs.Close()
	}
}
