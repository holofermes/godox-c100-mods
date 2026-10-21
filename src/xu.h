#ifndef XU_H
#define XU_H

/* The webcam's UVC extension unit (docs/webcam.md): one control that takes a
 * command line from the host. Shared by the descriptor (v6.S), the EP0 code
 * (xu.S) and the overlay (tick.c); plain defines, so assembler and C can both
 * include it. tools/patch.py and the tests carry the same values. */

#define XU_ID 6   /* bUnitID; stock's request tables know entities 0..5 */
#define XU_SEL 1  /* the one control selector, bit 0 of bmControls */
#define XU_LEN 32 /* bytes of the control: one line, NUL-padded; one EP0 packet */
#define XU_INFO 3 /* GET_INFO: GET and SET supported */

/* {21118ec3-89d6-4bb3-8015-7611dd2b1f87}, in descriptor byte order */
#define XU_GUID 0xc3, 0x8e, 0x11, 0x21, 0xd6, 0x89, 0xb3, 0x4b, \
		0x80, 0x15, 0x76, 0x11, 0xdd, 0x2b, 0x1f, 0x87

#endif /* XU_H */
