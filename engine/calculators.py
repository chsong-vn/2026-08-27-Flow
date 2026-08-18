
class FlowCalculator:
    def __init__(self, config):
        self.cfg = config

    def calculate_flows(self, concs, eqs, residence_time):
        """
        유속 계산

        @codesyncer-decision: 세척용매(농도 0) 허용
        - Port 1 (Solvent)는 농도가 0이므로 에러 대신 ratio=0으로 처리
        - 농도 0인 펌프는 stoichiometry 계산에서 제외됨 (순수 희석용)
        """
        import math as _math
        if residence_time <= 0:
            raise ValueError("체류 시간(Residence Time)은 0보다 커야 합니다.")
        # @codesyncer-decision: NaN/inf 입력 차단 (파라미터 경계 결함 fix)
        # - NaN 농도/당량이 들어오면 NaN 유속이 산출되어 카드/플랜에 전파됨
        for c in concs:
            if not _math.isfinite(c):
                raise ValueError(f"시약 농도에 유효하지 않은 값이 있습니다: {c}")
        for e in eqs:
            if not _math.isfinite(e):
                raise ValueError(f"당량에 유효하지 않은 값이 있습니다: {e}")
        # @codesyncer-decision: 농도 0 허용 (세척용매) - 음수만 에러
        if any(c < 0 for c in concs):
            raise ValueError("시약 농도(Concentration)는 음수일 수 없습니다.")
        # @codesyncer-decision: 당량 음수 차단 (fix)
        # - 기존: 음수 eq → 음수 유속 산출 → UI 카드에 음수 표시,
        #   엔진 2차 방어에 의존하던 것을 계산 단계에서 조기 차단
        if any(e < 0 for e in eqs):
            raise ValueError("당량(Equivalents)은 음수일 수 없습니다.")
        if len(concs) != len(eqs):
            raise ValueError("농도와 당량 데이터 개수가 일치하지 않습니다.")

        raw_flows = []
        for i in range(len(concs)):
            if concs[i] == 0:
                # 농도 0 = 세척용매: stoichiometry에 기여 안함
                ratio = 0.0
            else:
                ratio = eqs[i] / concs[i]
            raw_flows.append(ratio)

        # @codesyncer-decision(2026-08-14, 사용자 확정): 광화학의 '반응시간' = 빛 받은
        #   시간이므로 유속은 '조사부피' 기준이다. 반응기 총부피(암부 포함)를 쓰면
        #   같은 설정에서 조사 체류시간이 짧아진다(2.7 vs 2.4 = 11% 단축).
        #   수송 타이밍(t_head)은 반대로 총부피를 써야 하며 그쪽은 엔진이 담당.
        #   암부 미설정 config 는 조사=총 이라 기존 동작과 동일.
        _r_vol = float(getattr(self.cfg, "reactor_vol_illuminated", 0.0) or 0.0)
        if _r_vol <= 0:
            _r_vol = float(self.cfg.reactor_vol)
        target_total_flow = _r_vol / residence_time
        current_total_ratio = sum(raw_flows)
        
        if current_total_ratio <= 0: 
            return [0.0] * len(concs), 0.0
        
        scale_factor = target_total_flow / current_total_ratio
        final_flows = [f * scale_factor for f in raw_flows]
        
        return final_flows, target_total_flow

    def calculate_timings(self, pump_flows, total_flow, target_vol_ml):
        if total_flow <= 0: return {"injection_time": 0, "collection_duration": 0}
        injection_time = (target_vol_ml / total_flow) * 60.0
        collection_duration = injection_time 
        return {
            "injection_time": injection_time,
            "wait_for_collection": 0,
            "collection_duration": collection_duration
        }