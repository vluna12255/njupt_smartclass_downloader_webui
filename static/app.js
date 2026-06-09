window.currentConfig = {};
        let ws = null;
        let wsReconnectTimer = null;
        let wsOnline = false;

        // ── 任务状态存储（taskId -> taskData）──
        const taskStore = {};

        // ── WebSocket 连接管理 ──
        function connectWebSocket() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/ws/tasks`;
            try {
                ws = new WebSocket(wsUrl);

                ws.onopen = function() {
                    wsOnline = true;
                    if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
                    document.getElementById('server-status-bar')?.classList.add('hidden');
                    startHeartbeat();
                };

                ws.onmessage = function(event) {
                    if (event.data === 'pong') return;
                    try {
                        const msg = JSON.parse(event.data);
                        handleWebSocketMessage(msg);
                    } catch(e) { console.error('WS 消息解析失败:', e); }
                };

                ws.onerror = function() {};

                ws.onclose = function() {
                    wsOnline = false;
                    stopHeartbeat();
                    document.getElementById('server-status-bar')?.classList.remove('hidden');
                    wsReconnectTimer = setTimeout(connectWebSocket, 5000);
                };
            } catch(e) {
                wsReconnectTimer = setTimeout(connectWebSocket, 5000);
            }
        }

        let heartbeatTimer = null;
        function startHeartbeat() {
            stopHeartbeat();
            heartbeatTimer = setInterval(() => {
                if (ws && ws.readyState === WebSocket.OPEN) ws.send('ping');
            }, 30000);
        }
        function stopHeartbeat() {
            if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null; }
        }

        async function refreshTasksFromServer() {
            try {
                const response = await fetch('/api/tasks');
                const tasks = await response.json();
                tasks.forEach(t => { taskStore[t.id] = t; });
                renderTaskList();
            } catch (e) {
                console.error('任务列表刷新失败:', e);
            }
        }

        // ── WebSocket 消息分发 ──
        function handleWebSocketMessage(msg) {
            if (msg.type === 'task_list') {
                const tasks = msg.data || [];
                tasks.forEach(t => { taskStore[t.id] = t; });
                renderTaskList();
            } else if (msg.type === 'task_update') {
                const t = msg.data;
                taskStore[t.id] = t;
                renderOrUpdateTaskCard(t);
                if (t.id && t.id.startsWith('install_') && (t.status === 'completed' || t.status === 'failed')) {
                    const pluginName = t.id.replace('install_', '');
                    checkPluginStatus(pluginName, pluginColorMap[pluginName] || 'indigo', false);
                }
                // startup_ 任务完成/失败时，在设置界面中刷新插件状态徽章
                if (t.id && t.id.startsWith('startup_') && (t.status === 'completed' || t.status === 'failed')) {
                    const pluginName = t.id.replace('startup_', '');
                    const badge = document.getElementById(`plugin-status-badge-${pluginName}`);
                    if (badge) checkPluginStatus(pluginName, pluginColorMap[pluginName] || 'indigo', false);
                }
            } else if (msg.type === 'notification') {
                // 已禁用额外弹窗通知
            }
        }

        const pluginColorMap = { whisper: 'indigo', funasr: 'orange', slides_extractor: 'emerald' };

        const STATUS_CLASSES = {
            completed: 'bg-green-100 text-green-700',
            failed:    'bg-red-100 text-red-700',
            running:   'bg-blue-100 text-blue-700',
            waiting:   'bg-yellow-100 text-yellow-700',
            queued:    'bg-gray-100 text-gray-600',
        };
        const STATUS_TEXT = {
            completed: '已完成', failed: '失败', running: '进行中',
            waiting: '等待资源', queued: '排队中', cancelled: '已取消'
        };

        function escHtml(str) {
            return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
        }

        function buildTaskCardHTML(t) {
            const isInstall  = t.id && (t.id.startsWith('install_')  || (t.title && t.title.includes('安装')));
            const isStartup  = t.id && t.id.startsWith('startup_');
            const statusCls  = STATUS_CLASSES[t.status] || 'bg-gray-100 text-gray-600';
            const statusTxt  = t.status_text || STATUS_TEXT[t.status] || t.status;

            // 下载进度条：课程资源和插件模型下载都显示真实字节进度
            const showDlProgress = !isInstall && t.status === 'running' && t.total_size > 0
                && (!isStartup || t.current_action === '下载模型');
            const pct = t.total_size > 0 ? Math.round(t.downloaded_size / t.total_size * 100) : 0;

            // 模型加载阶段没有字节流，显示插件上报的阶段进度
            const showStartupProgress = isStartup && t.status === 'running' && t.total_size <= 0;
            const startupPct = Math.min(100, Math.max(0, t.progress || 0));

            // 光晕效果
            const glowHTML = (isInstall || isStartup) && t.status === 'running'
                ? `<div class="absolute top-0 right-0 -mt-4 -mr-4 w-24 h-24 bg-blue-50 rounded-full blur-2xl opacity-60 animate-pulse"></div>`
                : '';

            // 子标题行
            let sublineHTML = '';
            if (t.status === 'failed') {
                sublineHTML = `<span class="text-xs text-red-500 font-medium truncate">❌ ${escHtml(t.error || t.message || '')}</span>`;
            } else if (t.message) {
                let prefix = '';
                if (isInstall) prefix = `<span class="text-indigo-500">>></span> `;
                if (isStartup) prefix = '';
                sublineHTML = `<span class="text-xs text-gray-500 font-mono truncate transition-all">${prefix}${escHtml(t.message)}</span>`;
            }

            // 图标
            let iconHTML = '';
            if (isStartup) {
                iconHTML = '';
            } else if (isInstall) {
                iconHTML = `<div class="shrink-0 w-7 h-7 rounded-lg bg-blue-100 flex items-center justify-center text-blue-600">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/></svg>
                </div>`;
            }

            // 下载进度条
            const dlProgressHTML = showDlProgress ? `
                <div class="mt-3 flex items-center gap-3">
                    <div class="flex-grow h-1.5 bg-gray-100 rounded-full overflow-hidden">
                        <div class="h-full bg-blue-500 rounded-full transition-all duration-300" style="width:${pct}%"></div>
                    </div>
                    <div class="text-[10px] text-gray-400 font-mono whitespace-nowrap flex items-center gap-2">
                        <span>${escHtml(t.downloaded_str||'')}/${escHtml(t.total_size_str||'')}</span>
                        ${t.speed > 0 ? `<span class="w-px h-3 bg-gray-200"></span><span class="text-green-600 font-bold">${escHtml(t.speed_str||'')}</span>` : ''}
                    </div>
                </div>` : '';

            // 启动进度条（模型加载阶段）
            const startupProgressHTML = showStartupProgress ? `
                <div class="mt-3 flex items-center gap-3">
                    <div class="flex-grow h-1.5 bg-violet-100 rounded-full overflow-hidden">
                        <div class="h-full bg-violet-500 rounded-full transition-all duration-500" style="width:${startupPct}%"></div>
                    </div>
                    <span class="text-[10px] font-mono text-violet-500 whitespace-nowrap">${startupPct}%</span>
                </div>` : '';

            const progressHTML = dlProgressHTML + startupProgressHTML;

            return `
            <div class="bg-white px-5 py-4 rounded-xl shadow-sm border border-gray-200 relative overflow-hidden task-card" data-task-id="${escHtml(t.id)}">
                ${glowHTML}
                <div class="flex justify-between items-start relative z-10">
                    <div class="flex items-center gap-3 overflow-hidden mr-4 flex-1">
                        ${iconHTML}
                        <div class="flex flex-col min-w-0">
                            <span class="text-sm font-bold text-gray-800 truncate" title="${escHtml(t.title)}">${escHtml(t.title)}</span>
                            ${sublineHTML}
                        </div>
                    </div>
                    <span class="text-xs font-bold whitespace-nowrap px-2 py-1 rounded-md ${statusCls}">${escHtml(statusTxt)}</span>
                </div>
                ${progressHTML}
            </div>`;
        }

        function renderTaskList() {
            const loading = document.getElementById('task-list-loading');
            const inner = document.getElementById('task-list-inner');
            if (!inner) return;
            if (loading) loading.classList.add('hidden');
            inner.classList.remove('hidden');

            const tasks = Object.values(taskStore);
            if (tasks.length === 0) {
                inner.innerHTML = `
                <div class="max-w-4xl mx-auto p-4">
                    <div class="text-gray-400 text-sm py-12 border border-dashed border-gray-300 rounded-xl text-center bg-gray-50 flex flex-col items-center gap-2">
                        <svg class="w-8 h-8 opacity-20" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"></path></svg>
                        <span>暂无进行中的任务</span>
                    </div>
                </div>`;
                return;
            }
            const sorted = tasks.slice().sort((a, b) => {
                // startup_ 任务置顶（模型加载中最重要），其次 install_，再按状态排序
                const typeOrder = id => id && id.startsWith('startup_') ? 0 : id && id.startsWith('install_') ? 1 : 2;
                const tDiff = typeOrder(a.id) - typeOrder(b.id);
                if (tDiff !== 0) return tDiff;
                const order = { running:0, waiting:1, queued:2, failed:3, completed:4, cancelled:5 };
                return (order[a.status]||9) - (order[b.status]||9);
            });
            inner.innerHTML = `<div class="max-w-4xl mx-auto p-4"><div class="space-y-3">${sorted.map(buildTaskCardHTML).join('')}</div></div>`;
        }

        function renderOrUpdateTaskCard(t) {
            const inner = document.getElementById('task-list-inner');
            if (!inner || inner.classList.contains('hidden')) { renderTaskList(); return; }

            const existing = inner.querySelector(`[data-task-id="${CSS.escape(t.id)}"]`);
            if (existing) {
                const newCard = document.createElement('div');
                newCard.innerHTML = buildTaskCardHTML(t);
                const newEl = newCard.firstElementChild;
                existing.replaceWith(newEl);
            } else {
                // 新任务：重新渲染整个列表（保持排序）
                renderTaskList();
            }
        }

        let activeSearchController = null;

        function performSearch() {
            const input = document.getElementById('search-input');
            const keyword = input.value.trim();
            const searchBtn = document.querySelector('button[onclick="performSearch()"]');

            if (activeSearchController) {
                activeSearchController.abort();
                activeSearchController = null;
            }
            
            switchTab('search');
            
            if (!keyword) {
                // 如果搜索为空，显示初始状态
                showInitialState();
                return;
            }
            
            // 显示加载状态
            showSearchLoading();
            
            // 禁用搜索按钮并显示加载图标
            if (searchBtn) {
                searchBtn.disabled = true;
                searchBtn.innerHTML = `
                    <svg class="w-5 h-5 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                `;
            }
            
            // 执行搜索
            const formData = new FormData();
            formData.append('keyword', keyword);
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 40000);
            activeSearchController = controller;
            
            fetch('/search', {
                method: 'POST',
                body: formData,
                signal: controller.signal
            })
            .then(async response => {
                const html = await response.text();
                if (!response.ok) {
                    const error = new Error(`搜索请求失败: ${response.status}`);
                    error.html = html;
                    throw error;
                }
                return html;
            })
            .then(html => {
                document.getElementById('video-container').innerHTML = html;
                updateDownloadBtn();
            })
            .catch(error => {
                if (activeSearchController !== controller) return;
                console.error('搜索失败:', error);
                const timedOut = error.name === 'AbortError';
                showToast(timedOut ? '搜索超时，请稍后重试' : '搜索失败，请重试');
                document.getElementById('video-container').innerHTML = error.html || `
                    <div class="col-span-full flex flex-col items-center justify-center text-rose-500 py-24 animate-fade-in">
                        <div class="w-16 h-16 bg-rose-50 rounded-full flex items-center justify-center mb-4">
                            <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                            </svg>
                        </div>
                        <p class="text-base font-medium">${timedOut ? '搜索超时' : '搜索失败'}</p>
                        <p class="text-sm text-slate-400 mt-2">${timedOut ? 'SmartClass 响应较慢，请稍后重试' : '请检查网络连接后重试'}</p>
                    </div>
                `;
            })
            .finally(() => {
                clearTimeout(timeout);
                if (activeSearchController !== controller) return;
                activeSearchController = null;
                // 恢复搜索按钮
                if (searchBtn) {
                    searchBtn.disabled = false;
                    searchBtn.innerHTML = `
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
                        </svg>
                    `;
                }
            });
        }
        
        function showSearchLoading() {
            const container = document.getElementById('video-container');
            container.innerHTML = `
                <div class="col-span-full flex flex-col items-center justify-center text-slate-400 py-24 animate-fade-in">
                    <div class="relative w-20 h-20 mb-6">
                        <div class="absolute inset-0 border-4 border-blue-100 rounded-full"></div>
                        <div class="absolute inset-0 border-4 border-blue-500 rounded-full border-t-transparent animate-spin"></div>
                        <div class="absolute inset-3 bg-blue-50 rounded-full flex items-center justify-center">
                            <svg class="w-6 h-6 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
                            </svg>
                        </div>
                    </div>
                    <p class="text-lg font-medium text-slate-600 animate-pulse">正在搜索课程...</p>
                    <p class="text-sm text-slate-400 mt-2">请稍候</p>
                </div>
            `;
        }

        function showInitialState() {
            const container = document.getElementById('video-container');
            container.innerHTML = `
                <div class="col-span-full flex flex-col items-center justify-center text-slate-400 py-24 animate-fade-in select-none">
                    <div class="w-20 h-20 bg-white rounded-full flex items-center justify-center mb-5 shadow-sm border border-slate-100">
                        <svg class="w-8 h-8 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path></svg>
                    </div>
                    <p class="text-lg font-medium text-slate-500">输入关键词开始搜索</p>
                    <p class="text-sm text-slate-400 mt-2">支持搜索课程名称、教师姓名</p>
                </div>
            `;
            updateDownloadBtn();
        }

        window.addEventListener('load', async () => {
            // 连接 WebSocket
            connectWebSocket();
            
            // 搜索框 Enter 键监听
            const searchInput = document.getElementById('search-input');
            if (searchInput) {
                searchInput.addEventListener('keypress', function(e) {
                    if (e.key === 'Enter') {
                        e.preventDefault();
                        performSearch();
                    }
                });
            }
            
            await refreshConfig();
        });

        function switchTab(tabName) {
            document.querySelectorAll('.tab-btn.active').forEach(btn => {
                btn.classList.remove('active', 'shadow-sm');
                btn.classList.add('text-slate-500', 'hover:bg-slate-100');
            });
            const activeBtn = document.getElementById('tab-' + tabName);
            if(activeBtn) {
                activeBtn.classList.add('active', 'shadow-sm');
                activeBtn.classList.remove('text-slate-500', 'hover:bg-slate-100');
            }

            if (tabName === 'search') {
                document.getElementById('view-search').classList.remove('hidden');
                document.getElementById('view-tasks').classList.add('hidden');
            } else {
                document.getElementById('view-search').classList.add('hidden');
                document.getElementById('view-tasks').classList.remove('hidden');
                // WS 驱动，无需手动触发轮询
                renderTaskList();
            }
        }

        function togglePassword() {
            const input = document.getElementById('cfg-password');
            const iconOpen = document.getElementById('eye-icon-open');
            const iconClosed = document.getElementById('eye-icon-closed');
            
            if (input.type === 'password') {
                input.type = 'text';
                iconOpen.classList.remove('hidden');
                iconClosed.classList.add('hidden');
            } else {
                input.type = 'password';
                iconOpen.classList.add('hidden');
                iconClosed.classList.remove('hidden');
            }
        }

        // 服务器心跳：复用 WebSocket 连接状态，ws.onclose/onopen 已处理断线提示，无需额外 HTTP 轮询

        function toggleSelection(element) {
            const checkbox = element.querySelector('input[type="checkbox"]');
            checkbox.checked = !checkbox.checked;
            element.classList.toggle('selected', checkbox.checked);
            updateDownloadBtn();
        }

        function updateDownloadBtn() {
            const count = document.querySelectorAll('input[name="video_ids"]:checked').length;
            const btn = document.getElementById('download-btn');
            const countSpan = document.getElementById('selected-count');
            
            if(countSpan) countSpan.innerText = count;
            
            if (count > 0) {
                btn.classList.remove('hidden');
                btn.classList.add('flex');
            } else {
                btn.classList.add('hidden');
                btn.classList.remove('flex');
            }
        }

        async function refreshConfig() {
            try {
                const res = await fetch('/config');
                window.currentConfig = await res.json();
            } catch (e) { console.error("Config load failed", e); }
        }

        async function getDependencyStatus() {
            try {
                const res = await fetch('/api/plugins/dependency_check');
                return await res.json();
            } catch (e) {
                console.error("无法获取插件状态", e);
                return { whisper: false, funasr: false, slides_extractor: false };
            }
        }

        async function openDownloadModal() {
            await refreshConfig(); 
            const cfg = window.currentConfig;

            const status = await getDependencyStatus();
            const hasPPT = status.slides_extractor;
            const hasASR = status.whisper || status.funasr; 
            
            const count = document.querySelectorAll('input[name="video_ids"]:checked').length;
            const modalCount = document.getElementById('modal-count');
            if(modalCount) modalCount.innerText = count;

            const setControl = (id, shouldCheck, isEnabled) => { 
                const el = document.getElementById(id); 
                if(el) {
                    el.checked = isEnabled ? !!shouldCheck : false;
                    el.disabled = !isEnabled;
                    
                    const wrapper = el.closest('label') || el.parentElement;
                    if (wrapper) {
                        if (!isEnabled) {
                            wrapper.classList.add('opacity-50', 'cursor-not-allowed', 'grayscale');
                            wrapper.title = "请先在设置中安装对应插件";
                        } else {
                            wrapper.classList.remove('opacity-50', 'cursor-not-allowed', 'grayscale');
                            wrapper.title = "";
                        }
                    }
                }
            };

            setControl('dl-vga', cfg.default_vga, true); 
            setControl('dl-video1', cfg.default_video1, true);
            setControl('dl-video2', cfg.default_video2, true);
            
            setControl('dl-ppt', cfg.default_ppt, hasPPT);

            setControl('dl-whisper-vga', cfg.default_whisper_vga, hasASR);
            setControl('dl-whisper-video1', cfg.default_whisper_video1, hasASR);
            setControl('dl-whisper-video2', cfg.default_whisper_video2, hasASR);

            const modal = document.getElementById('download-modal');
            const content = document.getElementById('dl-modal-content');
            modal.classList.remove('hidden');
            setTimeout(() => {
                modal.classList.remove('opacity-0');
                content.classList.remove('scale-95');
                content.classList.add('scale-100');
            }, 10);
        }

        function closeModal() { 
            const modal = document.getElementById('download-modal');
            const content = document.getElementById('dl-modal-content');
            modal.classList.add('opacity-0');
            content.classList.remove('scale-100');
            content.classList.add('scale-95');
            setTimeout(() => { modal.classList.add('hidden'); }, 300);
        }

        async function confirmDownload() {
            const form = document.getElementById('video-form');
            const formData = new FormData(form);

            document.querySelectorAll('input[name="video_ids"]:checked').forEach(input => {
                formData.append('video_titles', input.dataset.videoTitle || input.value);
            });
            
            document.querySelectorAll('#download-modal input[name="file_types"]:checked').forEach(cb => formData.append('file_types', cb.value));
            
            if(document.getElementById('dl-whisper-vga')?.checked) formData.append('whisper_vga', 'true');
            if(document.getElementById('dl-whisper-video1')?.checked) formData.append('whisper_video1', 'true');
            if(document.getElementById('dl-whisper-video2')?.checked) formData.append('whisper_video2', 'true');

            // 获取当前设置中选择的引擎（即使没有保存）
            const engineRadio = document.querySelector('input[name="asr_engine"]:checked');
            if (engineRadio) {
                formData.append('asr_engine', engineRadio.value);
            }

            closeModal();
            try {
                const response = await fetch('/batch_download', { method: 'POST', body: formData });
                const result = await response.json();
                if (result.status === 'success') {
                    document.querySelectorAll('.video-card.selected').forEach(el => { el.classList.remove('selected'); el.querySelector('input').checked = false; });
                    updateDownloadBtn();
                    await refreshTasksFromServer();
                    switchTab('tasks');
                } else {
                    showToast('错误: ' + (result.msg || '未知错误'));
                }
            } catch (error) { showToast('网络请求失败'); }
        }

        function showToast(message) {
            const toast = document.getElementById('msg-toast');
            const text = document.getElementById('msg-toast-text');
            if (!toast || !text) return;
            
            text.innerText = message;
            toast.classList.remove('translate-y-10', 'opacity-0');
            
            if (window.toastTimer) clearTimeout(window.toastTimer);
            window.toastTimer = setTimeout(() => {
                toast.classList.add('translate-y-10', 'opacity-0');
            }, 3000);
        }

        window.openSettings = async function() {
            await refreshConfig();
            const cfg = window.currentConfig;
            
            let user = "", pass = "";
            if (cfg.auth && cfg.auth.username) { 
                user = cfg.auth.username; 
                pass = cfg.auth.password || ""; 
            } else if (cfg.username) { 
                user = cfg.username; 
                pass = cfg.password || ""; 
            }
            document.getElementById('cfg-username').value = user;
            document.getElementById('cfg-password').value = pass;
            
            document.getElementById('cfg-auto-login').checked = !!cfg.auto_login;

            document.getElementById('cfg-def-vga').checked = !!cfg.default_vga;
            document.getElementById('cfg-def-video1').checked = !!cfg.default_video1;
            document.getElementById('cfg-def-video2').checked = !!cfg.default_video2;
            
            document.getElementById('cfg-def-whisper-vga').checked = !!cfg.default_whisper_vga;
            document.getElementById('cfg-def-whisper-video1').checked = !!cfg.default_whisper_video1;
            document.getElementById('cfg-def-whisper-video2').checked = !!cfg.default_whisper_video2;

            document.getElementById('cfg-def-ppt').checked = !!cfg.default_ppt;
            document.getElementById('cfg-download-dir').value = cfg.download_dir || "";
            
            const status = await getDependencyStatus();
            let currentEngine = cfg.asr_engine || 'whisper';

            const isWhisperOk = status.whisper;
            const isFunasrOk = status.funasr;
            const isCurrentOk = status[currentEngine]; 

            if (!isCurrentOk) {
                if (isWhisperOk && !isFunasrOk) {
                    currentEngine = 'whisper'; 
                } else if (isFunasrOk && !isWhisperOk) {
                    currentEngine = 'funasr';  
                }
            }
            
            const engineRadio = document.querySelector(`input[name="asr_engine"][value="${currentEngine}"]`);
            if(engineRadio) engineRadio.checked = true;

            const wRadio = document.querySelector('input[name="asr_engine"][value="whisper"]');
            if (wRadio) {
                wRadio.disabled = !isWhisperOk;
                wRadio.parentElement.style.opacity = isWhisperOk ? "1" : "0.5";
                wRadio.parentElement.style.cursor = isWhisperOk ? "pointer" : "not-allowed";
            }

            const fRadio = document.querySelector('input[name="asr_engine"][value="funasr"]');
            if (fRadio) {
                fRadio.disabled = !isFunasrOk;
                fRadio.parentElement.style.opacity = isFunasrOk ? "1" : "0.5";
                fRadio.parentElement.style.cursor = isFunasrOk ? "pointer" : "not-allowed";
            }

            const hasAnyAsr = status.whisper || status.funasr;
            const hasPPT = status.slides_extractor;

            const toggleSettingDisabled = (id, isEnabled) => {
                const el = document.getElementById(id);
                if (el) {
                    el.disabled = !isEnabled;
                    if (!isEnabled) el.checked = false;
                    
                    const wrapper = el.parentElement; 
                    if(wrapper) {
                        wrapper.style.opacity = isEnabled ? "1" : "0.5";
                        wrapper.style.cursor = isEnabled ? "pointer" : "not-allowed";
                    }
                }
            }

            toggleSettingDisabled('cfg-def-whisper-vga', hasAnyAsr);
            toggleSettingDisabled('cfg-def-whisper-video1', hasAnyAsr);
            toggleSettingDisabled('cfg-def-whisper-video2', hasAnyAsr);

            toggleSettingDisabled('cfg-def-ppt', hasPPT);

            // 初始检查时显示"检测中"
            checkPluginStatus('whisper', 'indigo', true);
            checkPluginStatus('funasr', 'orange', true);
            checkPluginStatus('slides_extractor', 'emerald', true);

            document.getElementById('settings-modal').classList.remove('hidden');
        };
        
        // 插件轮询已移除：由 WebSocket task_update 事件驱动
        function closeSettings() {
            document.getElementById('settings-modal').classList.add('hidden');
        }

        async function saveSettings() {         
            const status = await getDependencyStatus();
            const hasAnyAsr = status.whisper || status.funasr;
            const hasPPT = status.slides_extractor;

            const engineRadio = document.querySelector('input[name="asr_engine"]:checked');
            let asrEngine = engineRadio ? engineRadio.value : 'whisper';

            if (asrEngine === 'whisper' && !status.whisper && status.funasr) asrEngine = 'funasr';
            if (asrEngine === 'funasr' && !status.funasr && status.whisper) asrEngine = 'whisper';

            const data = {
                asr_engine: asrEngine, 
                download_dir: document.getElementById('cfg-download-dir').value.trim(),
                auto_login: document.getElementById('cfg-auto-login').checked,

                default_vga: document.getElementById('cfg-def-vga').checked,
                default_video1: document.getElementById('cfg-def-video1').checked,
                default_video2: document.getElementById('cfg-def-video2').checked,

                default_whisper_vga: hasAnyAsr ? document.getElementById('cfg-def-whisper-vga').checked : false,
                default_whisper_video1: hasAnyAsr ? document.getElementById('cfg-def-whisper-video1').checked : false,
                default_whisper_video2: hasAnyAsr ? document.getElementById('cfg-def-whisper-video2').checked : false,
                
                default_ppt: hasPPT ? document.getElementById('cfg-def-ppt').checked : false,
                
                auto_whisper: false 
            };

            // 只在启用自动登录时才提交用户名和密码
            const username = document.getElementById('cfg-username').value;
            const password = document.getElementById('cfg-password').value;
            if (data.auto_login && username && password) {
                data.username = username;
                data.password = password;
            }

            await fetch('/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            });
            
            await refreshConfig();
            document.getElementById('settings-modal').classList.add('hidden');
        }

        async function checkPluginStatus(pluginName, colorClass, isInitialCheck = false) {
            const badge = document.getElementById(`plugin-status-badge-${pluginName}`);
            const btnInstall = document.getElementById(`btn-install-${pluginName}`);
            const btnUninstall = document.getElementById(`btn-uninstall-${pluginName}`);
            const hint = document.getElementById(`plugin-installing-hint-${pluginName}`);
            
            if (!badge || !btnInstall) return;

            // 只在初始检查时显示"检测中"，避免闪烁
            if (isInitialCheck) {
                badge.innerText = "检测中...";
                badge.className = "px-2 py-1 rounded text-[10px] font-bold bg-gray-200 text-gray-500";
                btnInstall.classList.add('hidden');
                if (btnUninstall) btnUninstall.classList.add('hidden');
                if (hint) hint.classList.add('hidden');
            }

            try {
                const res = await fetch(`/api/plugins/${pluginName}/status`);
                if (!res.ok) throw new Error("API Error");
                const data = await res.json();
                
                // 获取当前状态，避免不必要的 DOM 更新
                const currentBadgeText = badge.innerText;
                const currentBadgeClass = badge.className;
                
                let newBadgeText = "";
                let newBadgeClass = "";
                let shouldShowInstall = false;
                let shouldShowUninstall = false;
                let shouldShowHint = false;
                let uninstallDisabled = false;
                let uninstallText = "一键卸载";
                
                if (data.uninstalling) {
                    newBadgeText = "卸载中";
                    newBadgeClass = "px-2 py-1 rounded text-[10px] font-bold bg-orange-100 text-orange-600 animate-pulse";
                    uninstallDisabled = true;
                    uninstallText = "卸载中...";
                    shouldShowUninstall = true;
                } else if (data.installing) {
                    newBadgeText = "处理中";
                    newBadgeClass = `px-2 py-1 rounded text-[10px] font-bold bg-${colorClass}-100 text-${colorClass}-600`;
                    shouldShowHint = true;
                } else if (data.installed === true) {
                    newBadgeText = "已安装";
                    newBadgeClass = "px-2 py-1 rounded text-[10px] font-bold bg-green-100 text-green-600";
                    shouldShowUninstall = true;
                    uninstallDisabled = false;
                    uninstallText = "一键卸载";
                } else {
                    newBadgeText = "未安装";
                    newBadgeClass = "px-2 py-1 rounded text-[10px] font-bold bg-rose-100 text-rose-600";
                    shouldShowInstall = true;
                }
                
                // 只在状态真正改变时更新 DOM，减少闪烁
                if (currentBadgeText !== newBadgeText) {
                    badge.innerText = newBadgeText;
                }
                if (currentBadgeClass !== newBadgeClass) {
                    badge.className = newBadgeClass;
                }
                
                // 更新按钮显示状态
                if (shouldShowInstall) {
                    btnInstall.classList.remove('hidden');
                } else {
                    btnInstall.classList.add('hidden');
                }
                
                if (btnUninstall) {
                    if (shouldShowUninstall) {
                        btnUninstall.classList.remove('hidden');
                        btnUninstall.disabled = uninstallDisabled;
                        if (btnUninstall.innerText !== uninstallText) {
                            btnUninstall.innerText = uninstallText;
                        }
                    } else {
                        btnUninstall.classList.add('hidden');
                    }
                }
                
                if (hint) {
                    if (shouldShowHint) {
                        hint.classList.remove('hidden');
                    } else {
                        hint.classList.add('hidden');
                    }
                }
            } catch (e) {
                console.error(`${pluginName} check failed:`, e);
                if (badge.innerText !== "状态未知") {
                    badge.innerText = "状态未知";
                }
                btnInstall.classList.remove('hidden');
            }
        }

        async function uninstallPlugin(pluginName) {
            if (!confirm(`确定要卸载 ${pluginName} 环境吗？\n卸载后再次使用需要重新下载。`)) {
                return;
            }

            const btnUninstall = document.getElementById(`btn-uninstall-${pluginName}`);
            const badge = document.getElementById(`plugin-status-badge-${pluginName}`);
            
            // 立即更新UI，不等待
            if (btnUninstall) {
                btnUninstall.disabled = true;
                btnUninstall.innerText = "卸载中...";
                btnUninstall.blur();
            }
            if (badge) {
                badge.innerText = "卸载中";
                badge.className = "px-2 py-1 rounded text-[10px] font-bold bg-orange-100 text-orange-600 animate-pulse";
            }

            // 使用 setTimeout 让 UI 先更新，然后再发送请求
            setTimeout(async () => {
                try {
                    const res = await fetch(`/api/plugins/${pluginName}/uninstall`, { method: 'POST' });
                    const data = await res.json();
                    
                    if (res.ok && data.status === 'success') {
                        await checkPluginStatus(pluginName, 'gray', false);
                        await refreshConfig();

                        const status = await getDependencyStatus();
                        const wRadio = document.querySelector('input[name="asr_engine"][value="whisper"]');
                        if (wRadio) {
                            wRadio.disabled = !status.whisper;
                            wRadio.parentElement.style.opacity = status.whisper ? "1" : "0.5";
                            wRadio.parentElement.style.cursor = status.whisper ? "pointer" : "not-allowed";
                        }
                        const fRadio = document.querySelector('input[name="asr_engine"][value="funasr"]');
                        if (fRadio) {
                            fRadio.disabled = !status.funasr;
                            fRadio.parentElement.style.opacity = status.funasr ? "1" : "0.5";
                            fRadio.parentElement.style.cursor = status.funasr ? "pointer" : "not-allowed";
                        }

                    } else {
                        showToast("卸载失败: " + (data.msg || "未知错误"));
                        if (btnUninstall) {
                            btnUninstall.disabled = false;
                            btnUninstall.innerText = "一键卸载";
                        }
                        if (badge) {
                            badge.innerText = "已安装";
                            badge.className = "px-2 py-1 rounded text-[10px] font-bold bg-green-100 text-green-600";
                        }
                    }
                } catch (e) {
                    console.error(e);
                    showToast("请求超时或服务未响应，请稍后刷新页面查看");
                    if (btnUninstall) btnUninstall.innerText = "请刷新查看";
                }
            }, 0);
        }

        async function installPlugin(pluginName) {
            const btn = document.getElementById(`btn-install-${pluginName}`);
            btn.disabled = true;
            btn.innerText = "请求中...";
            try {
                const res = await fetch(`/api/plugins/${pluginName}/install`, { method: 'POST' });
                
                if (!res.ok) throw new Error("Request failed");

                const data = await res.json();
                if (data.status === 'success') {
                    document.getElementById('settings-modal').classList.add('hidden');
                    switchTab('tasks');
                } else {
                    showToast("安装失败: " + (data.msg || "未知错误"));
                    btn.disabled = false;
                    btn.innerText = "一键安装";
                }
            } catch(e) {
                showToast("请求错误: 请检查后端服务是否正常");
                btn.disabled = false;
                btn.innerText = "一键安装";
            }
        }
