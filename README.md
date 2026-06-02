# 南邮智慧课堂下载器 WebUI

面向南京邮电大学智慧课堂的本地 WebUI 下载工具。当前版本已将主程序重构为 Go，下载、任务调度、插件管理和本地网页服务由单个 Windows 可执行文件负责；字幕识别与 PPT 提取按需安装为独立 Python 插件。

## 功能

- 批量检索并下载课程视频、PPT 和字幕
- 使用 aria2 下载，支持队列、进度展示和失败重试
- 可选安装 Whisper、FunASR 字幕插件
- 可选安装 PPT 提取插件，并在任务列表中展示启动与处理状态
- 自动准备插件所需的 Python、VC++ Runtime 和 FFmpeg
- 登录凭据写入 Windows 凭据管理器，不以明文形式保存在配置文件中

## 使用方式

项目目前主要面向 Windows。普通用户可以从 GitHub Releases 下载已经构建好的 `SmartClassDownloader.exe`，将它放在本项目资源目录的根目录后直接运行。

首次运行会自动打开浏览器，并在程序目录中创建 `config/`、`logs/`、`runtime/`、`plugins_env/` 和 `SmartclassDownload/` 等运行时目录。首次下载时，程序会按需获取 aria2；首次安装插件时，程序会下载插件运行环境和依赖，因此需要保持网络连接。

常用启动参数：

```powershell
.\SmartClassDownloader.exe
.\SmartClassDownloader.exe -no-browser
.\SmartClassDownloader.exe -port 8081
.\SmartClassDownloader.exe -log-level debug
```

## 从源码构建

需要 Go 1.22 或更高版本。

```powershell
cd app
$env:GOAMD64="v3"; go build -trimpath -o ..\SmartClassDownloader.exe ./cmd/smartclass-downloader
```

构建脚本会先执行测试，再在项目根目录生成 `SmartClassDownloader.exe`。

开发时可以直接运行：

```powershell
cd app
go run ./cmd/smartclass-downloader -root .. -no-browser
```

## 插件说明

插件可在 WebUI 中按需安装：

- `whisper`：基于 faster-whisper 的字幕识别
- `funasr`：基于 FunASR 的中文字幕识别
- `slides_extractor`：从课程视频中提取 PPT


## 目录结构

```text
app/             Go 主程序源码
plugins/            可选 Python 插件源码
static/             WebUI 静态资源
templates/          WebUI 页面模板
LICENSE             开源许可证
```

运行后生成的日志位于 `logs/`。如需排查问题，可以使用 `-log-level debug` 启动程序。

## 致谢

项目基于 [QIN2DIM/njupt-smartclass-downloader](https://github.com/QIN2DIM/njupt-smartclass-downloader) 的相关思路开发，并使用 aria2 等第三方组件。详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 免责声明

本项目仅用于学习与个人课程资料整理。请遵守学校平台规则和相关法律法规，不要传播受版权保护的课程内容。

