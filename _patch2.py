# -*- coding: utf-8 -*-
path = r'd:\1111111\app\src\services\task_manager.py'

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = (
    '                            # 通知所有等待的任务：服务已就绪

'
    '                            event.set()

'
    '                            logger.info(f"Task {task_id}: {target_plugin_name} 服务就绪，通知其他等待任务")

'
    '                        else:

'
    '                            # 其他任务等待通知，不重复启动服务

'
    '                            logger.info(f"Task {task_id}: 等待 {target_plugin_name} 被其他任务启动...")

'
    '                            self._update_task(task_id, status=TaskStatus.WAITING,

'
    '                                            message=f"等待 {target_plugin_name} 服务就绪...",

'
    '                                            current_action="等待服务")

'
    '                            if not event.wait(timeout=620):

'
    '                                raise Exception(f"{target_plugin_name} 服务等待超时")

'
    '                            logger.info(f"Task {task_id}: {target_plugin_name} 已就绪，继续执行")

'
)

new = (
    '                            # 检查启动任务卡片：若模型加载失败，whisper 服务虽能响应 /docs

'
    '                            # 但 global_model 为 None，需在此拦截并将下载任务标记为失败

'
    '                            _startup_tid = f"startup_{target_plugin_name}"

'
    '                            with self.lock:

'
    '                                _st = self.tasks.get(_startup_tid)

'
    '                                _startup_failed = _st is not None and _st.status == TaskStatus.FAILED

'
    '                                _startup_error = _st.error if _st else ""

'
    '                            if _startup_failed:

'
    '                                # 唤醒所有正在 wait 的非 starter 任务，让它们立即感知失败

'
    '                                event.set()

'
    '                                with self._plugin_ready_lock:

'
    '                                    self._plugin_ready_events.pop(target_plugin_name, None)

'
    '                                raise Exception(f"{target_plugin_name} 模型加载失败: {_startup_error}")

'
    '

'
    '                            # 通知所有等待的任务：服务已就绪

'
    '                            event.set()

'
    '                            logger.info(f"Task {task_id}: {target_plugin_name} 服务就绪，通知其他等待任务")

'
    '                        else:

'
    '                            # 其他任务等待通知，不重复启动服务

'
    '                            logger.info(f"Task {task_id}: 等待 {target_plugin_name} 被其他任务启动...")

'
    '                            self._update_task(task_id, status=TaskStatus.WAITING,

'
    '                                            message=f"等待 {target_plugin_name} 服务就绪...",

'
    '                                            current_action="等待服务")

'
    '                            if not event.wait(timeout=620):

'
    '                                raise Exception(f"{target_plugin_name} 服务等待超时")

'
    '                            # event 被 set 后，检查是否因模型加载失败触发（starter 失败时会先 set 再 pop）

'
    '                            _startup_tid = f"startup_{target_plugin_name}"

'
    '                            with self.lock:

'
    '                                _st = self.tasks.get(_startup_tid)

'
    '                                _startup_failed = _st is not None and _st.status == TaskStatus.FAILED

'
    '                                _startup_error = _st.error if _st else ""

'
    '                            if _startup_failed:

'
    '                                raise Exception(f"{target_plugin_name} 模型加载失败: {_startup_error}")

'
    '                            logger.info(f"Task {task_id}: {target_plugin_name} 已就绪，继续执行")

'
)

if old not in content:
    print('ERROR: old string not found in file')
else:
    new_content = content.replace(old, new, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print('OK: patch applied successfully')
