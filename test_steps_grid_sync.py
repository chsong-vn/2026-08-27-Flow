# 스텝 테이블 뷰 동기화 로직 검증 (GUI 없이): 그리드 셀 편집 → sequence_data + StepCard
import types
import ui.tab_sequence as ts
from ui.tab_sequence import _rnum

class Spin:
    def __init__(self,v=0.0): self.v=v
    def setValue(self,x): self.v=x
    def value(self): return self.v
    def blockSignals(self,b): pass
class Combo:
    def __init__(self): self.idx=-1; self._data={}
    def findData(self,d):
        for i,v in self._data.items():
            if v==d: return i
        return -1
    def setCurrentIndex(self,i): self.idx=i
    def blockSignals(self,b): pass
class Card:
    def __init__(self,pumps):
        self.sp_temp=Spin(); self.sp_rt=Spin(); self.sp_vol=Spin(); self.sp_tube=Spin()
        self.pump_widgets={}
        for gi,p in enumerate(pumps):
            cb=Combo(); cb._data={j:j+1 for j in range(12)}  # index j -> port j+1
            self.pump_widgets[p]={'cb_port':cb,'sp_eq':Spin()}
class Cfg: INLET_PUMPS=["Group A","Group_B"]
class App:
    def __init__(self): self.cfg=Cfg(); self.map_mgr=None; self.is_dark_mode=True

class FakeSeq: pass
for m in ("_steps_rows","_steps_columns","_on_steps_grid_cell"):
    setattr(FakeSeq,m,getattr(ts.SequenceTab,m))

seq=FakeSeq(); seq.app=App(); seq.steps_grid=None; seq._steps_syncing=False
pumps=Cfg.INLET_PUMPS
seq.sequence_data=[{'temp':25.0,'rt':10.0,'vol':5.0,'tube_vol':1.5,
    'pumps':{p:{'port':2,'eq':1.0,'flow':0.075} for p in pumps}} for _ in range(2)]
seq.step_cards=[Card(pumps) for _ in range(2)]
seq._mark_flow_dirty=lambda *a,**k: None
seq.calc_flow=lambda *a,**k: None
seq._refresh_steps_grid=lambda *a,**k: None

ok=True
def chk(c,m):
    global ok; print(("PASS" if c else "FAIL")+": "+m); ok=ok and c

# 1) _steps_columns 구조 (그룹 컬럼)
cols=seq._steps_columns()
grp=[c for c in cols if 'columns' in c]
chk(len(grp)==2 and grp[0]['title']=="Group A" and grp[0]['columns'][0]['field']=="p0_port", "컬럼 그룹(펌프별 포트/당량)")
chk(cols[0]['field']=="step" and cols[-1]['field']=="flow", "# ~ flow 배치")

# 2) _steps_rows: sequence_data → 행 dict
rows=seq._steps_rows()
chk(len(rows)==2 and rows[0]['_id']=="0" and rows[0]['step']==1, "행 id/step")
chk(rows[0]['temp']==25.0 and rows[0]['p0_port']==2 and rows[0]['p1_eq']==1.0, "조건/펌프 값 매핑")
chk(rows[0]['flow']==round(0.075*2,3), "총유속 합산")

# 3) 셀 편집 → sequence_data + 카드 반영
seq._on_steps_grid_cell({"_id":"1","temp":"80","p0_port":"5","p1_eq":"2.5"})
sd=seq.sequence_data[1]; card=seq.step_cards[1]
chk(sd['temp']==80.0, "온도 편집 → sequence_data")
chk(card.sp_temp.value()==80.0, "온도 편집 → StepCard sp_temp")
chk(sd['pumps']['Group A']['port']==5, "포트 편집 → sequence_data")
chk(card.pump_widgets['Group A']['cb_port'].idx==4, "포트 편집 → 카드 콤보 index(포트5=idx4)")
chk(sd['pumps']['Group_B']['eq']==2.5 and card.pump_widgets['Group_B']['sp_eq'].value()==2.5, "당량 편집 → 데이터+카드")
chk(sd['rt']==10.0, "미변경 필드 보존")

# 4) 포트 클램프 (1~12)
seq._on_steps_grid_cell({"_id":"0","p0_port":"99"})
chk(seq.sequence_data[0]['pumps']['Group A']['port']==12, "포트 99 → 12 클램프")

print("\n=== "+("ALL PASS" if ok else "SOME FAIL")+" ===")
import sys; sys.exit(0 if ok else 1)
