# Writeups Reverse — Exemples de challenges résolus

## Analyse statique rapide
```bash
file binary
strings binary | grep -i "flag\|ctf{\|key\|pass"
ltrace ./binary
strace ./binary
objdump -d binary | grep -A5 "main\|check\|verify"
```

## Ghidra (pyghidra)
```python
import pyghidra
with pyghidra.open_program('/challenge/distfiles/binary') as flat_api:
    from ghidra.app.decompiler import DecompInterface
    ifc = DecompInterface()
    ifc.openProgram(flat_api.currentProgram)
    func = flat_api.getFunction('main')
    res = ifc.decompileFunction(func, 60, None)
    print(res.getDecompiledFunction().getC())
```

## radare2
```bash
r2 -A binary
# Dans r2:
afl          # liste fonctions
pdf @main    # decompile main
pdf @sym.check_password
iz           # strings
/iz flag     # chercher "flag" dans strings
```

## Crackme — brute force le flag
```python
import subprocess
flag = "CTF{"
charset = "abcdefghijklmnopqrstuvwxyz0123456789_}"
while not flag.endswith("}"):
    for c in charset:
        result = subprocess.run(['./binary', flag + c], capture_output=True)
        if b"correct" in result.stdout or result.returncode == 0:
            flag += c
            break
```

## Anti-debug bypass
```bash
# Patcher les appels ptrace
# Dans gdb: set follow-fork-mode child
# ltrace pour voir les checks
ltrace -e ptrace ./binary
```

## Obfuscation XOR key
```python
data = open('binary','rb').read()
# Chercher XOR key par frequency analysis
# Flag commence souvent par CTF{ donc
# key[0] = data[offset] ^ ord('C')
```

## angr — resolution symbolique
```python
import angr
proj = angr.Project('./binary', auto_load_libs=False)
state = proj.factory.entry_state()
simgr = proj.factory.simulation_manager(state)
simgr.explore(find=0xADDR_SUCCESS, avoid=0xADDR_FAIL)
if simgr.found:
    print(simgr.found[0].posix.dumps(0))  # stdin
```
