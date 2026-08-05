"""원격 명령 폴링 — 외부 프로세스와의 파일 기반 인터페이스

@codesyncer-decision: main.py 책임 분리 (믹스인 패턴)
- 기존 main.py(941줄)에 UI 조립·모니터링·운전제어·핫리로드·원격명령이
  전부 집중되어 변경 영향 범위를 추적하기 어려웠음
- 탭/매니저들이 `app.속성`으로 강결합되어 있어 컴포지션 전환은 수백 개
  참조 수정이 필요 → 믹스인으로 self 네임스페이스를 보존하면서 파일만 분리
  (행동 100% 보존, 단계적 후속 리팩토링의 발판)
- 메서드 본문은 main.py에서 그대로 이동 (수정 없음)
"""

import os


class RemoteCmdMixin:
    """remote_cmd.txt 파일 기반 원격 명령 (navigate/theme/pause/estop)"""

    def _poll_remote_cmd(self):
        """원격 명령 파일을 감시하고 실행한다."""
        try:
            if not os.path.exists(self._remote_cmd_file):
                return
            with open(self._remote_cmd_file, "r", encoding="utf-8") as f:
                cmd = f.read().strip()
            if not cmd:
                return
            os.remove(self._remote_cmd_file)
            print(f">>> 원격 명령 수신: {cmd}")
            self._exec_remote_cmd(cmd)
        except Exception as e:
            print(f"!!! 원격 명령 오류: {e}")

    def _exec_remote_cmd(self, cmd):
        """원격 명령을 파싱하고 실행한다."""
        parts = cmd.split(None, 1)
        action = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if action == "navigate":
            pages = {"dashboard": 0, "sequence": 1, "manual": 2, "calculator": 3}
            idx = pages.get(arg.lower())
            if idx is not None:
                self._switch_main_page(idx)
                print(f">>> 페이지 전환: {arg}")
        elif action == "theme":
            self.toggle_theme()
        elif action == "pause":
            self.btn_p.setChecked(True)
            self.tog_pause(True)
        elif action == "resume":
            self.btn_p.setChecked(False)
            self.tog_pause(False)
        elif action == "estop":
            self.estop()
        else:
            print(f">>> 알 수 없는 원격 명령: {cmd}")
