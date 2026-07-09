# Homelab Blender Rack Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a dimensioned Blender model and rendered previews for the homelab rack using the current 18U direction plus a 15U comparison.

**Architecture:** Generate the scene from one Python script so the U layout, device dimensions, labels, cabling, and aesthetic panels are repeatable. The model includes two rack variants, rack-mounted devices, rails/shelves, white Etherlighting-style patch cables, Rackstuds on light gear, and an apple plus room-height reference.

**Tech Stack:** Blender Python, Cycles/Eevee materials, local PNG renders, `.blend` output.

---

### Task 1: Build Scripted Model

**Files:**
- Create: `/Users/goon/polymarket/homelab_blender_model/build_homelab_rack.py`

- [ ] **Step 1: Define dimensions**

Use 1U = 44.45 mm, 19-inch rack rail width, 18U and 15U mounting heights, 24-inch rack depth baseline, Sliger CX4170a = 4U x 17-inch depth, Sliger CX4712 = 4U x 25-inch depth, UniFi Pro Max 16 = 325.1 x 160 x 43.7 mm, UniFi patch panel = 1U, QNAP TS-433eU = 1U x 292.3 mm depth, CyberPower OR500LCDRM1U = 433 x 44 x 235 mm, and StarTech PDU = 1U.

- [ ] **Step 2: Draw rack equipment**

Create reusable helpers for rack frame posts, 1U panels, 4U cases, drive bays, switch ports, keystone panel ports, and shelf devices.

- [ ] **Step 3: Add cabling and aesthetic parts**

Add 16 white curved patch cables between the Pro Max 16 and patch panel, blue/white emissive Etherlighting plugs, purple LED strip glow, smoked/frosted acrylic side panels, and red/black Rackstuds on light 1U gear.

- [ ] **Step 4: Add scale references**

Add a normal room-height ruler and an apple near the rack so the size reads correctly.

### Task 2: Render Outputs

**Files:**
- Create: `/Users/goon/polymarket/homelab_blender_model/homelab_rack_15u_18u.blend`
- Create: `/Users/goon/polymarket/homelab_blender_model/homelab_rack_18u_render.png`
- Create: `/Users/goon/polymarket/homelab_blender_model/homelab_rack_15u_vs_18u_render.png`

- [ ] **Step 1: Run Blender headlessly**

Run: `/Applications/Blender.app/Contents/MacOS/Blender --background --python /Users/goon/polymarket/homelab_blender_model/build_homelab_rack.py`

- [ ] **Step 2: Verify files**

Run: `ls -lh /Users/goon/polymarket/homelab_blender_model`

- [ ] **Step 3: Inspect renders**

Open the PNGs locally and check that TS-433eU-US is 1U, the UPS is 1U, the top patch row uses one Pro Max 16 and one patch panel, and both 4U systems are shown.
