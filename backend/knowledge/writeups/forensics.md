# Writeups Forensics — Exemples de challenges résolus

## Stego — ZIP caché dans JPG
**Challenge**: ImaginaryCTF — hidden zip with steghide
**Technique**: Un ZIP chiffre est concatene a la fin d'un JPG
```bash
# Detecter
binwalk -e file.jpg
# ou
strings file.jpg | grep -i zip
# Extraire manuellement
zip -FF file.jpg --out extracted.zip
# Mot de passe visible dans l'image (faible opacite)
# Regarder l'image avec contrast eleve
convert file.jpg -contrast-stretch 0 enhanced.jpg
# Steghide avec le mot de passe trouve
steghide extract -sf file.jpg -p "MOTDEPASSE"
```
**Flag**: `ictf{if_only_steghide_worked_without_depreciated_libs_from_a_decade_ago}`

## PNG — LSB Steganographie
```bash
zsteg file.png
# ou
python3 -c "
from PIL import Image
img = Image.open('file.png')
pixels = list(img.getdata())
bits = [p[0] & 1 for p in pixels]
msg = bytes(int(''.join(map(str,bits[i:i+8])),2) for i in range(0,len(bits),8))
print(msg[:100])
"
```

## PCAP — Extraire fichiers
```bash
tshark -r capture.pcap --export-objects http,./extracted/
tshark -r capture.pcap -Y "ftp-data" -T fields -e data | xxd -r -p > extracted
# Chercher flags
strings capture.pcap | grep -i "flag\|ctf{"
tshark -r capture.pcap -Y "http" -T fields -e http.request.uri -e http.file_data
```

## Memory forensics
```bash
vol -f mem.raw windows.info
vol -f mem.raw windows.pslist
vol -f mem.raw windows.cmdline
vol -f mem.raw windows.filescan | grep -i flag
vol -f mem.raw windows.dumpfiles --virtaddr 0xADDR
```

## Fichier corrompu — Magic bytes
```python
# PNG magic: 89 50 4E 47 0D 0A 1A 0A
# JPG magic: FF D8 FF
# ZIP magic: 50 4B 03 04
# PDF magic: 25 50 44 46
with open('corrupt.png','r+b') as f:
    f.seek(0)
    f.write(b'\x89PNG\r\n\x1a\n')
```

## Disk image
```bash
mmls disk.img          # partitions
fls -r -o OFFSET disk.img  # fichiers
icat -o OFFSET disk.img INODE > recovered
```
