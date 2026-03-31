# -*- coding: utf-8 -*-
import sys
path = r"d:\1111111\app\src\services\task_manager.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Use \r\n since file is CRLF
CR = "\r\n"

old_lines = [
    "                            # \u901a\u77e5\u6240\u6709\u7b49\u5f85\u7684\u4efb\u52a1\uff1a\u670d\u52a1\u5df2\u5c31\u7eea",
    "                            event.set()",
    "                            logger.info(f\"Task {task_id}: {target_plugin_name} \u670d\u52a1\u5c31\u7eea\uff0c\u901a\u77e5\u5176\u4ed6\u7b49\u5f85\u4efb\u52a1\")",
    "                        else:",
    "                            # \u5176\u4ed6\u4efb\u52a1\u7b49\u5f85\u901a\u77e5\uff0c\u4e0d\u91cd\u590f\u542f\u52a8\u670d\u52a1",
    "                            logger.info(f\"Task {task_id}: \u7b49\u5f85 {target_plugin_name} \u88ab\u5176\u4ed6\u4efb\u52a1\u542f\u52a8...\")",
    "                            self._update_task(task_id, status=TaskStatus.WAITING,",
    "                                            message=f\"\u7b49\u5f85 {target_plugin_name} \u670d\u52a1\u5c31\u7eea...\",",
    "                                            current_action=\"\u7b49\u5f85\u670d\u52a1\")",
    "                            if not event.wait(timeout=620):",
    "                                raise Exception(f\"{target_plugin_name} \u670d\u52a1\u7b49\u5f85\u8d85\u65f6\")",
    "                            logger.info(f\"Task {task_id}: {target_plugin_name} \u5df2\u5c31\u7eea\uff0c\u7ee7\u7eed\u6267\u884c\")",
]
old = CR.join(old_lines) + CR

new_lines = [
    "                            # \u68c0\u67e5\u542f\u52a8\u4efb\u52a1\u5361\u7247\uff1a\u82e5\u6a21\u578b\u52a0\u8f7d\u5931\u8d25\uff0c\u670d\u52a1\u867d\u80fd\u54cd\u5e94 /docs",
    "                            # \u4f46 global_model \u4e3a None\uff0c\u9700\u5728\u6b64\u62e6\u622a\u5e76\u5c06\u4efb\u52a1\u6807\u8bb0\u4e3a\u5931\u8d25",
    "                            _startup_tid = f\"startup_{target_plugin_name}\"",
    "                            with self.lock:",
    "                                _st = self.tasks.get(_startup_tid)",
    "                                _startup_failed = _st is not None and _st.status == TaskStatus.FAILED",
    "                                _startup_error = _st.error if _st else \"\"",
    "                            if _startup_failed:",
    "                                # \u5524\u9192\u6240\u6709\u6b63\u5728 wait \u7684\u975e starter \u4efb\u52a1",
    "                                event.set()",
    "                                with self._plugin_ready_lock:",
    "                                    self._plugin_ready_events.pop(target_plugin_name, None)",
    "                                raise Exception(f\"{target_plugin_name} \u6a21\u578b\u52a0\u8f7d\u5931\u8d25: {_startup_error}\")",
    "",
    "                            # \u901a\u77e5\u6240\u6709\u7b49\u5f85\u7684\u4efb\u52a1\uff1a\u670d\u52a1\u5df2\u5c31\u7eea",
    "                            event.set()",
    "                            logger.info(f\"Task {task_id}: {target_plugin_name} \u670d\u52a1\u5c31\u7eea\uff0c\u901a\u77e5\u5176\u4ed6\u7b49\u5f85\u4efb\u52a1\")",
    "                        else:",
    "                            # \u5176\u4ed6\u4efb\u52a1\u7b49\u5f85\u901a\u77e5\uff0c\u4e0d\u91cd\u590f\u542f\u52a8\u670d\u52a1",
    "                            logger.info(f\"Task {task_id}: \u7b49\u5f85 {target_plugin_name} \u88ab\u5176\u4ed6\u4efb\u52a1\u542f\u52a8...\")",
    "                            self._update_task(task_id, status=TaskStatus.WAITING,",
    "                                            message=f\"\u7b49\u5f85 {target_plugin_name} \u670d\u52a1\u5c31\u7eea...\",",
    "                                            current_action=\"\u7b49\u5f85\u670d\u52a1\")",
    "                            if not event.wait(timeout=620):",
    "                                raise Exception(f\"{target_plugin_name} \u670d\u52a1\u7b49\u5f85\u8d85\u65f6\")",
    "                            # event \u88ab set \u540e\uff0c\u68c0\u67e5\u662f\u5426\u56e0\u6a21\u578b\u52a0\u8f7d\u5931\u8d25\u89e6\u53d1",
    "                            _startup_tid = f\"startup_{target_plugin_name}\"",
    "                            with self.lock:",
    "                                _st = self.tasks.get(_startup_tid)",
    "                                _startup_failed = _st is not None and _st.status == TaskStatus.FAILED",
    "                                _startup_error = _st.error if _st else \"\"",
    "                            if _startup_failed:",
    "                                raise Exception(f\"{target_plugin_name} \u6a21\u578b\u52a0\u8f7d\u5931\u8d25: {_startup_error}\")",
    "                            logger.info(f\"Task {task_id}: {target_plugin_name} \u5df2\u5c31\u7eea\uff0c\u7ee7\u7eed\u6267\u884c\")",
]
new = CR.join(new_lines) + CR

if old not in content:
    print("ERROR: old string not found")
    # debug: show context
    idx = content.find("\u901a\u77e5\u6240\u6709\u7b49\u5f85\u7684\u4efb\u52a1")
    print(f"Found marker at index: {idx}")
    if idx >= 0:
        snippet = content[idx-5:idx+200]
        print(repr(snippet[:300]))
else:
    result = content.replace(old, new, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(result)
    print("OK: patch applied")
