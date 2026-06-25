# Writeups Crypto — Exemples de challenges résolus

## ECC — Ordre de courbe spécial + Pohlig-Hellman
**Challenge**: ImaginaryCTF — Solovay-Strassen weak primality
```python
from sage.all import *
p = 36328006888239989979835683756179050367864581
a = 28662185558404391344601135531809588907013812
b = 16171080326069448853813193139376819675521374
E = EllipticCurve(GF(p), [a, b])
# Pohlig-Hellman si ordre B-smooth
G = E.lift_x(5777125167305480814518525709561856971293800)
```

## RSA — Petits exposants (Hastad broadcast)
```python
from sage.all import *
M3 = CRT([c1, c2, c3], [n1, n2, n3])
m = integer_nth_root(3, M3)
```

## RSA — Wiener (d petit)
```python
# RsaCtfTool --attack wiener
RsaCtfTool -n N -e E --attack wiener
```

## RSA — Factorisation ECM
```python
from sage.all import *
print(ecm.factor(n))
```

## AES CBC — Padding Oracle
```python
# Modifier le bloc precedent octet par octet
# Detecter padding correct via erreur/timing
```

## XOR — Known plaintext
```python
key = bytes(a^b for a,b in zip(ciphertext, known_plaintext))
```

## Hash length extension (SHA1/MD5/SHA256)
```bash
# hashpump -s HASH -d DATA -a APPEND -k KEYLEN
```

## Vigenere
```python
# Indice de coincidence pour trouver la longueur de cle
# Puis frequency analysis par position
```
