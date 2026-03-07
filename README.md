# 南邮智慧课堂下载器 WebUI

本项目是基于 `ArcticLampyrid/njupt_smartclass_downloader` 核心逻辑开发的 Web 可视化版本。旨在为南京邮电大学学生提供一个易于使用的智慧课堂视频下载工具。

## 功能特性

* **可视化任务管理**：通过 Web 界面直观管理下载任务。
* **批量并发下载**：支持一键解析课程列表，多线程并发下载视频资源。
* **智能字幕生成**：集成 Faster-Whisper 与 FunASR，为课程生成中文字幕。
* **课件提取**：通过视频关键帧分析算法，自动提取 PPT 内容并导出为 PDF 文档。

---

## 安装与运行

**适用操作系统**：Windows 10/11

### 方式一：使用预编译程序

1. 访问项目 [Releases](https://github.com/vluna12255/njupt_smartclass_downloader_webui/releases) 页面。
2. 下载 `.exe` 安装包（**注意**：需要使用插件功能推荐下载 full 版，该版本已内置 FFmpeg、Python 运行环境及 VC++ 库）。
3. 运行安装包并启动程序。

### 方式二：源码编译

**1. 克隆项目**

```bash
git clone https://github.com/vluna12255/njupt_smartclass_downloader_webui.git
cd njupt_smartclass_downloader_webui

```

**2. 环境配置**
本项目使用 Poetry 进行依赖管理：

```bash
# 配置虚拟环境
poetry config virtualenvs.in-project true

# 安装依赖
poetry install

```

**3. 编译打包**

使用 PyInstaller 生成可执行文件：

```bash
poetry run pyinstaller SmartClassDownloader.spec

```

使用 Nuitka 生成可执行文件：

```bash
poetry run python build_nuitka.py

```

> **重要提示**：
> 编译完成后，需将编译生成的文件以及文件夹移动至项目根目录，确保 `templates/` 文件夹位于可执行文件同一级目录下，否则无法加载 Web 界面。

---

## 使用指南

启动程序后，请使用浏览器访问 `http://localhost:8080` 进入管理界面。

1. **身份认证**：在登录页面输入南邮统一身份认证的账号和密码。
2. **插件安装**：点击界面右上角的“设置”，选择所需组件，点击“安装”并等待下载完成。
3. **创建下载任务**：进入搜索页面搜索课程并单击选中；选择完视频后点击右下角“批量下载”，勾选所需项目并点击“开始下载”加入任务队列。
4. **文件管理**：任务完成后，文件默认保存在项目根目录下的 `SmartclassDownloader` 文件夹中。可以在设置页面修改默认下载路径。
5. **关闭程序**：直接关闭任务所在的命令行窗口，或在任务所在的命令行窗口输入ctrl+c。

---

## 项目结构

```text
.
├── app/
│   ├── src/
│   │   ├── api/                # API 路由模块
│   │   ├── core/               # 核心功能模块
│   │   ├── models/             # 数据模型
│   │   ├── plugins/            # 插件系统
│   │   ├── services/           # 业务服务层
│   │   └── utils/              # 工具模块
│   └── server.py               # Web 服务入口
├── bin/                        # 二进制工具
├── config/                     # 用户配置文件存储
├── logs/                       # 日志文件目录
├── plugins/                    # 插件目录
├── runtime/                    # 运行环境
└── templates/                  # 前端 HTML 模板资源

```

---

## 致谢

本项目登录逻辑、视频流解析及 PPT 提取算法等参考了以下开源项目，特此感谢原作者的贡献：

* **Core Logic**: `njupt_smartclass_downloader`
* **Original Author**: ArcticLampyrid

---


### License

Licensed under **GNU Affero General Public License v3.0** or later. See `LICENSE` for more information.

### Disclaimer

 This tool is for educational purposes only. Users are responsible for complying with NJUPT's terms of service and applicable laws. The authors are not responsible for any misuse of this software.

