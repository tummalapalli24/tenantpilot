# Acme Installation Troubleshooting Guide

This guide covers fault codes raised by Acme SmartLink controllers during installation and commissioning of rooftop units (RTUs) and split systems. Field engineers and dispatchers use it to decide the next action after a failed or blocked install.

## E-ACT-401: Missing activation token

The controller refuses commissioning because the work order was created without an activation token. Every SmartLink controller must be provisioned by the Provisioning team, which issues a one-time activation token tied to the controller serial number. When the token is missing, the unit powers on but stays unprovisioned, so the building has no climate control from the new equipment.

Most common cause: the installation work order was booked directly in the Field Portal without submitting a provisioning request first. Less common: the token expired (tokens are valid for 14 days) or was issued for a different controller serial.

Resolution steps:

1. Confirm the controller serial number on the work order matches the unit on site.
2. Request a new activation token through the Provisioning queue in the Field Portal (standard SLA 4 business hours; urgent requests for P1/P2 customers are handled within 1 hour).
3. Rebook the technician for the earliest window after the token is issued and re-run commissioning.
4. Verify the controller reports "online" in the SmartLink dashboard before closing the job.
5. Tell the customer the expected restore time and log the delay reason on the work order.

## E-FW-118: Firmware checksum mismatch

The controller rejected a firmware package because its checksum did not match the signed manifest. This usually means the download was interrupted on a weak cellular link, not that the package is malicious.

Resolution steps:

1. Pause the rollout for the affected site; leave healthy controllers on their current version.
2. Re-download firmware package 4.2.1 over the site's wired uplink or a hotspot with a stable signal.
3. Verify the checksum in the SmartLink installer before flashing.
4. Retry the update on the failed controllers one at a time.

## E-SNS-210: Sensor drift

A zone temperature sensor disagrees with the reference probe by more than 2°F. Drift above 2°F on temperature-sensitive zones (pharmacy, lab, server rooms) must be corrected the same day.

Resolution steps:

1. Compare the sensor against a calibrated reference probe for 10 minutes.
2. Apply the offset in the controller if drift is under 4°F; replace the sensor if it is 4°F or more.
3. Record before/after readings on the work order.

## When to escalate

Escalate to the on-call dispatcher when an installation leaves a site without climate control for more than 4 hours, when the same fault code repeats after one retry, or when the customer is healthcare, education, or senior living.
