package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"
	"time"

	"smartclassdownloader/internal/bootstrap"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run() error {
	root := flag.String("root", "", "application data root; defaults to the repository or executable directory")
	port := flag.Int("port", 8080, "preferred loopback HTTP port")
	noBrowser := flag.Bool("no-browser", false, "do not open the default browser")
	logLevel := flag.String("log-level", "info", "log level: debug, info, warn, or error")
	flag.Parse()

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	application, err := bootstrap.NewApplication(ctx, bootstrap.Options{
		RootDir: *root, Port: *port, OpenBrowser: !*noBrowser, LogLevel: *logLevel,
	})
	if err != nil {
		return err
	}
	defer application.CloseLogs()
	if err := application.Start(ctx); err != nil {
		return err
	}
	<-ctx.Done()
	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer shutdownCancel()
	return application.Shutdown(shutdownCtx)
}
