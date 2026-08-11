# 어댑터가 외부(method_io/reagent_excel/계산기 import)의 QTableWidget 호출을
# 정확히 흉내내는지 검증 (GUI 없이).
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import types
import ui.tab_sequence as ts
from ui.tab_sequence import _ReagentGridAdapter, _rnum

class QTWI:  # QTableWidgetItem 흉내 (외부 코드가 setItem에 넘기는 것)
    def __init__(self, s=""): self._t = str(s)
    def text(self): return self._t
    def setFlags(self,*a): pass

class FakeMap:
    def __init__(self): self.inlet={}
    def update_inlet(self, pump, port, name, conc, smiles=""):
        self.inlet[(pump,int(port))] = (name, float(conc), smiles)
    def get_inlet(self, pump, port):
        n,c,s = self.inlet.get((pump,int(port)), ("",1.0,""))
        return {"name":n,"conc":c,"smiles":s}

class FakeGrid:
    def __init__(self): self.last=None; self.count=0
    def set_rows(self, rows): self.last=rows; self.count+=1

class FakeApp:
    def __init__(self): self.map_mgr=FakeMap(); self.is_dark_mode=True

class FakeSeq: pass
for m in ("_reagent_cell_defaults","_reagent_row_dict","_push_reagent_rows"):
    setattr(FakeSeq, m, getattr(ts.SequenceTab, m))

seq = FakeSeq()
seq.app = FakeApp()
seq.reagent_grid = FakeGrid()

PUMP="Chemyx ID:1"
ad = _ReagentGridAdapter(seq, PUMP, 0)
seq.reagent_tables = {PUMP: ad}

ok = True
def check(cond, msg):
    global ok
    print(("PASS" if cond else "FAIL")+": "+msg)
    if not cond: ok=False

# 1) rowCount/columnCount
check(ad.rowCount()==12 and ad.columnCount()==4, "rowCount=12, columnCount=4")

# 2) 초기 예약행: 포트1 Solvent, 포트12 Waste
check(ad.item(0,0).text()=="1" and ad.item(0,1).text()=="세척 용매", "port1 Solvent 기본값")
check(ad.item(11,0).text()=="12" and ad.item(11,1).text()=="폐기", "port12 Waste 기본값")

# 3) 계산기 Import 패턴: blockSignals + item().setText() → map 동기화 + 1회 flush
seq.reagent_grid.count=0
ad.blockSignals(True)
ad.item(1,1).setText("Aryl bromide")   # port2 name
ad.item(1,2).setText("0.2")            # port2 conc
ad.blockSignals(False)
check(seq.app.map_mgr.inlet.get((PUMP,2))==("Aryl bromide",0.2,""), "port2 map 동기화 (import)")
check(seq.reagent_grid.count==1, "blockSignals 동안 flush 억제 → 해제 시 1회만")
check(seq.reagent_grid.last is not None and len(seq.reagent_grid.last)==12, "flush=12행 푸시")

# 4) reagent_excel 패턴: setItem(QTableWidgetItem) 3열
ad.blockSignals(True)
ad.setItem(2,1,QTWI("Amine")); ad.setItem(2,2,QTWI("0.4")); ad.setItem(2,3,QTWI("NC1CC1"))
ad.blockSignals(False)
check(seq.app.map_mgr.inlet.get((PUMP,3))==("Amine",0.4,"NC1CC1"), "port3 map (excel, smiles 포함)")

# 5) reagent_excel 저장 패턴: item(r,c).text() 읽기
check(ad.item(2,1).text()=="Amine" and ad.item(2,2).text()=="0.4" and ad.item(2,3).text()=="NC1CC1", "item().text() 읽기 일관")

# 6) 예약행(포트1/12)은 map 미동기화
before=dict(seq.app.map_mgr.inlet)
ad.item(0,1).setText("커스텀용매")
check((PUMP,1) not in seq.app.map_mgr.inlet, "port1 편집은 map 미동기화(표시만)")
check(ad.item(0,1).text()=="커스텀용매", "port1 표시값은 갱신")

# 7) 비블록 즉시 flush
seq.reagent_grid.count=0
ad.item(4,1).setText("Test")   # port5
check(seq.reagent_grid.count==1, "비블록 setText 즉시 flush")

# 8) row_dict 구조 (그리드 행 dict)
rd = seq._reagent_row_dict(0, PUMP, 2, ad._cells)
check(rd["_id"]=="0:2" and rd["grp"]==PUMP and rd["gk"]=="a" and rd["ro"]==False and rd["conc"]==0.2, "row_dict 구조")
rd12 = seq._reagent_row_dict(0, PUMP, 12, ad._cells)
check(rd12["ro"]==True, "port12 ro=True")

print("\n=== "+("ALL PASS" if ok else "SOME FAIL")+" ===")
import sys; sys.exit(0 if ok else 1)
