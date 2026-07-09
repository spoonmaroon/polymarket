Title:
Help optimize my first 15U aesthetic homelab rack: media server, firewall, ML/trading compute, two reused gaming PCs

Post:
I am planning my first real homelab rack and would like feedback before I buy the rack/cases/networking parts. I am trying to keep it compact and clean-looking, ideally black/white with subtle RGB and clean cable management, but I still want it to be practical.

Main goals:
- Media server for legally owned media, likely Jellyfin or Plex
- Good home firewall/router setup with VLANs and ad blocking
- ML/trading/backtesting workloads
- Docker containers and monitoring
- Ubuntu-first setup, with a persistent Windows VM on the main workstation
- Reuse my existing PCs as much as possible

Current hardware:
- PC 1:
  - Ryzen 7 7700X
  - ASUS PRIME B650M-A AX6 II, micro-ATX
  - 32GB DDR5
  - 1TB SSD
  - RTX 5060 Ti
  - Role: Ubuntu desktop, Windows VM, gaming, ML/dev

- PC 2:
  - i7-12700F
  - MSI PRO B660M-A CEC WIFI DDR4 (MS-7D37), micro-ATX
  - 16GB DDR4
  - 1TB SSD
  - RTX 3060 Ti
  - I may also have another 3060 Ti available
  - Role: Docker/Jellyfin/trading services/secondary GPU compute

- Mini PC:
  - HP ProDesk 400 G
  - i5-7500T
  - 28GB RAM
  - 480GB SSD
  - Role: always-on low-power services like AdGuard/Pi-hole, Tailscale, Uptime Kuma, maybe Home Assistant

Proposed rack layout:
```text
U15    Patch panel / cable brush
U14    MikroTik CRS310-8G+2S+IN switch
U13    Shelf: OPNsense firewall box + HP ProDesk
U12    QNAP TS-433eU-US NAS
U11    Vented blank / airflow gap
U10-U7 PC 1 in Sliger CX4170a
U6     Vented blank / airflow gap
U5-U2  PC 2 in Sliger CX4170a
U1     Power / vent only; UPS probably outside the rack
```

NAS/storage plan:
- Considering QNAP TS-433eU-US as a compact 1U 4-bay NAS
- I know it is not a heavy compute box: ARM CPU, 4GB non-upgradeable RAM, dual 2.5GbE
- It would mainly serve SMB/media/backups/datasets
- Thinking 2x16TB NAS drives to start, then expand to 4x16TB later
- I already have a 2TB Seagate drive, but I would probably use that as scratch/offline backup instead of putting it in the main NAS pool

Compute/rack case plan:
- 2x Sliger CX4170a cases for the two PCs
- Both motherboards are micro-ATX, so they should fit
- I still need to check GPU length, CPU cooler height, PSU length, and front-panel connector compatibility
- I understand CX4170a is 17 inches deep, so I should avoid shallow 18 inch cabinets and get a deeper rack with real rear cable clearance

Networking/security plan:
- Dedicated OPNsense firewall appliance, probably 4x 2.5GbE
- MikroTik CRS310-8G+2S+IN for 8x 2.5GbE and 2x 10G SFP+
- Use 10GbE for the two main PCs if needed
- QNAP TS-433eU-US would stay on 2.5GbE
- VLANs planned: Main, Servers, Media, Trading/ML, IoT, Guest, Management
- Remote access via Tailscale or WireGuard, not exposing NAS/admin ports directly

Upgrade plan:
- PC 1: upgrade to 64GB DDR5 minimum, maybe 128GB if worthwhile
- PC 2: upgrade to 64GB DDR4 minimum
- Add 2-4TB NVMe to each PC
- UPS, likely outside the rack unless the rack is rated for the total weight
- Add vented blanks, brush panel, labels, Velcro ties, and short white patch cables

Questions:
1. Does this role split make sense: QNAP for storage, OPNsense box for firewall, PCs for compute, HP ProDesk for low-power services?
2. Is the QNAP TS-433eU-US too weak/limited for this plan if I only use it as storage/media/backups?
3. Would you pick 12TB or 16TB drives for a 4-bay NAS? I am leaning 16TB because 4 bays fills up fast.
4. Is the MikroTik CRS310 a good fit here, or should I use a different 10GbE/2.5GbE switch?
5. Any concerns with two Sliger CX4170a cases in a 15U rack from an airflow/noise/fit perspective?
6. Should PC 2 run Ubuntu Server directly, or would Proxmox make more sense for Docker/Jellyfin/trading services?
7. Is dual GPU in the micro-ATX i7-12700F system worth trying, or should I keep one GPU per PC?
8. What rack depth/load rating would you consider minimum for this setup?
9. Any better compact/aesthetic rack or case recommendations before I buy?

I am new to this, so I am mainly trying to avoid buying parts that technically fit on paper but are annoying in real life. I care about the rack looking clean, but I would rather fix the architecture now than build something pretty and dumb.
