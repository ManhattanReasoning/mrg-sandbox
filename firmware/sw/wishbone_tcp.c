/*
 * wishbone_tcp.c -- generic Wishbone-over-TCP bridge firmware
 *
 * Exposes the user design's Wishbone region over TCP port 1234 using the
 * wire protocol defined in orchestrator/.../workers/protocol.py. The
 * firmware knows nothing about any particular user design: it executes
 * raw bus transactions, so a single firmware image serves every design.
 *
 * Request (big-endian):
 *   Byte 0:     opcode  (0x01 = write, 0x02 = read)
 *   Bytes 1-3:  length
 *                 write: number of 32-bit data words that follow
 *                 read:  encoded in the first (and only) data word
 *   Bytes 4-7:  address (byte offset within the user region)
 *   Bytes 8+:   data    (32-bit words; write: values, read: [word count])
 *
 * Response (big-endian):
 *   Byte 0:     status  (0x00 = ok, 0x01 = error)
 *   Bytes 1-3:  length  (number of 32-bit words that follow)
 *   Bytes 4+:   data
 *
 * Errors (out-of-range address/length, unknown opcode) return status 0x01
 * with zero data words. Addresses are offsets into the user region only;
 * the rest of the SoC bus is unreachable from the network by design.
 */

#include <stdint.h>
#include <string.h>

#include <generated/csr.h>
#include <generated/mem.h>

#include "lwip/init.h"
#include "lwip/netif.h"
#include "lwip/timeouts.h"
#include "lwip/tcp.h"
#include "lwip/ip4_addr.h"
#include "lwip/etharp.h"
#include "netif/ethernet.h"

err_t ethernetif_init(struct netif *netif);
void  ethernetif_input(struct netif *netif);

/* ---- Network configuration --------------------------------------------- */

/*
 * Per-FPGA static IP.  FPGA_ID (0-9) is supplied at build time (-DFPGA_ID=,
 * default 0); the host's config.py uses the same formula: 192.168.1.(101 +
 * fpga_id).
 */
#ifndef FPGA_ID
#define FPGA_ID 0
#endif

#define FPGA_IP0  192
#define FPGA_IP1  168
#define FPGA_IP2    1
#define FPGA_IP3  (101 + FPGA_ID)

#define BRIDGE_PORT  1234

/* ---- User design region ------------------------------------------------- */

#define USER_REGION_WORDS  (USER_SIZE / 4)   /* 512 */

static volatile uint32_t *user_region = (volatile uint32_t *)USER_BASE;

/* ---- Protocol constants -------------------------------------------------- */

#define OP_WRITE     0x01
#define OP_READ      0x02
#define STATUS_OK    0x00
#define STATUS_ERROR 0x01

#define HEADER_LEN   8
/* Largest legal request: write of all 512 words. */
#define REQ_BUF_LEN  (HEADER_LEN + USER_REGION_WORDS * 4)
/* Largest legal response: read of all 512 words. */
#define RESP_BUF_LEN (4 + USER_REGION_WORDS * 4)

static uint8_t req_buf[REQ_BUF_LEN];
static uint32_t req_fill;            /* bytes accumulated so far */
static uint8_t resp_buf[RESP_BUF_LEN];
static uint32_t resp_len;            /* total bytes of current response */
static uint32_t resp_queued;         /* bytes handed to tcp_write so far */

/* ---- Big-endian helpers (VexRiscv is little-endian) ---------------------- */

static uint32_t be32_load(const uint8_t *p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8)  | (uint32_t)p[3];
}

static void be32_store(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v >> 24);
    p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);
    p[3] = (uint8_t)v;
}

static uint32_t be24_load(const uint8_t *p)
{
    return ((uint32_t)p[0] << 16) | ((uint32_t)p[1] << 8) | (uint32_t)p[2];
}

static void be24_store(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v >> 16);
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)v;
}

/* ---- Request handling ----------------------------------------------------
 *
 * Returns the response length in resp_buf.
 */

static uint32_t make_error(void)
{
    resp_buf[0] = STATUS_ERROR;
    be24_store(resp_buf + 1, 0);
    return 4;
}

static uint32_t handle_request(const uint8_t *req, uint32_t req_len)
{
    uint8_t  op     = req[0];
    uint32_t length = be24_load(req + 1);
    uint32_t addr   = be32_load(req + 4);

    /* Addresses must be word-aligned byte offsets inside the user region. */
    if (addr & 3)
        return make_error();
    uint32_t word_addr = addr / 4;

    if (op == OP_WRITE) {
        if (req_len != HEADER_LEN + length * 4)
            return make_error();
        if (length == 0 || word_addr + length > USER_REGION_WORDS)
            return make_error();

        for (uint32_t i = 0; i < length; i++)
            user_region[word_addr + i] = be32_load(req + HEADER_LEN + i * 4);

        resp_buf[0] = STATUS_OK;
        be24_store(resp_buf + 1, 0);
        return 4;
    }

    if (op == OP_READ) {
        /* Reads carry the word count as a single data word. */
        if (req_len != HEADER_LEN + 4 || length != 1)
            return make_error();
        uint32_t count = be32_load(req + HEADER_LEN);
        if (count == 0 || word_addr + count > USER_REGION_WORDS)
            return make_error();

        resp_buf[0] = STATUS_OK;
        be24_store(resp_buf + 1, count);
        for (uint32_t i = 0; i < count; i++)
            be32_store(resp_buf + 4 + i * 4, user_region[word_addr + i]);
        return 4 + count * 4;
    }

    return make_error();
}

/* Expected total size of the request currently in req_buf, or 0 if the
 * header is not complete yet. */
static uint32_t expected_request_len(void)
{
    if (req_fill < HEADER_LEN)
        return 0;
    uint8_t  op     = req_buf[0];
    uint32_t length = be24_load(req_buf + 1);
    if (op == OP_READ)
        return HEADER_LEN + 4;                 /* one word: the read count */
    return HEADER_LEN + length * 4;            /* write payload */
}

/* ---- Chunked response sending ---------------------------------------------
 *
 * A full 512-word read response (2052 bytes) does not fit lwIP's small
 * heap in one tcp_write(COPY). Queue as much as fits now; the tcp_sent
 * callback queues the rest as ACKs free buffer space.
 */

static void process_buffer(struct tcp_pcb *pcb);

static void send_pending(struct tcp_pcb *pcb)
{
    while (resp_queued < resp_len) {
        uint32_t rem = resp_len - resp_queued;
        uint32_t chunk = tcp_sndbuf(pcb);
        if (chunk == 0)
            break;
        /* A single tcp_write(COPY) larger than one segment fails (ERR_MEM)
         * and queues nothing on this small-heap build, so cap each write at
         * one MSS; tcp_sent drains the remainder as the window opens. */
        if (chunk > TCP_MSS)
            chunk = TCP_MSS;
        if (chunk > rem)
            chunk = rem;
        err_t e = tcp_write(pcb, resp_buf + resp_queued, (u16_t)chunk,
                            TCP_WRITE_FLAG_COPY);
        if (e != ERR_OK)
            break;                  /* heap full: retry from tcp_sent */
        resp_queued += chunk;
    }
    tcp_output(pcb);
}

static err_t bridge_sent(void *arg, struct tcp_pcb *pcb, u16_t len)
{
    (void)arg;
    (void)len;
    if (resp_queued < resp_len) {
        send_pending(pcb);
    } else {
        /* Response fully queued; handle any pipelined request. */
        process_buffer(pcb);
    }
    return ERR_OK;
}

/* Process complete requests in req_buf. Stops while a response is still
 * being drained -- bridge_sent resumes processing afterwards. */
static void process_buffer(struct tcp_pcb *pcb)
{
    for (;;) {
        if (resp_queued < resp_len)            /* previous response in flight */
            return;

        uint32_t want = expected_request_len();
        if (want == 0 || req_fill < want)
            return;
        if (want > sizeof(req_buf)) {          /* oversized: reject, resync */
            req_fill = 0;
            resp_len = make_error();
            resp_queued = 0;
            send_pending(pcb);
            return;
        }

        resp_len = handle_request(req_buf, want);
        resp_queued = 0;

        /* Shift any trailing bytes of the next request to the front. */
        req_fill -= want;
        if (req_fill > 0)
            memmove(req_buf, req_buf + want, req_fill);

        send_pending(pcb);

        /* Bit 3 (D9): response queued */
        debug_leds_out_write(debug_leds_out_read() | 0x08);
    }
}

/* ---- TCP callbacks -------------------------------------------------------- */

static err_t bridge_recv(void *arg, struct tcp_pcb *pcb, struct pbuf *p, err_t err)
{
    (void)arg;

    if (err != ERR_OK || p == NULL) {
        req_fill = 0;
        resp_len = resp_queued = 0;
        tcp_close(pcb);
        if (p != NULL)
            pbuf_free(p);
        return ERR_OK;
    }

    /* Bit 2 (D8): data received */
    debug_leds_out_write(debug_leds_out_read() | 0x04);

    /* Accumulate -- requests may span TCP segments, and one segment may
     * carry several back-to-back requests. */
    for (struct pbuf *q = p; q != NULL; q = q->next) {
        uint32_t copy = q->len;
        if (req_fill + copy > sizeof(req_buf))
            copy = sizeof(req_buf) - req_fill;
        memcpy(req_buf + req_fill, q->payload, copy);
        req_fill += copy;
    }
    tcp_recved(pcb, p->tot_len);
    pbuf_free(p);

    process_buffer(pcb);

    return ERR_OK;
}

static err_t bridge_accept(void *arg, struct tcp_pcb *new_pcb, err_t err)
{
    (void)arg;
    if (err != ERR_OK || new_pcb == NULL)
        return ERR_VAL;

    req_fill = 0;                              /* fresh framing per connection */
    resp_len = resp_queued = 0;                /* no response in flight yet */
    tcp_recv(new_pcb, bridge_recv);
    tcp_sent(new_pcb, bridge_sent);            /* drives chunked-send drain */
    return ERR_OK;
}

/* ---- Main ----------------------------------------------------------------- */

int main(void)
{
    /* Bit 0 (D6): main() reached */
    debug_leds_out_write(0x01);

    timer0_en_write(0);
    timer0_reload_write(0xffffffff);
    timer0_load_write(0xffffffff);
    timer0_en_write(1);

    lwip_init();

    /* Bit 1 (D7): lwIP initialised */
    debug_leds_out_write(0x03);

    struct netif netif;
    ip4_addr_t ipaddr, netmask, gateway;
    IP4_ADDR(&ipaddr,  FPGA_IP0, FPGA_IP1, FPGA_IP2, FPGA_IP3);
    IP4_ADDR(&netmask, 255, 255, 255, 0);
    IP4_ADDR(&gateway, 192, 168,   1,   1);

    netif_add(&netif, &ipaddr, &netmask, &gateway,
              NULL, ethernetif_init, ethernet_input);
    /* 0x04: after netif_add */
    debug_leds_out_write(0x04);

    netif_set_default(&netif);
    netif_set_up(&netif);
    /* 0x05: after netif_set_up (gratuitous ARP may fire here) */
    debug_leds_out_write(0x05);

    struct tcp_pcb *listen_pcb = tcp_new();
    /* 0x06: after tcp_new */
    debug_leds_out_write(0x06);

    tcp_bind(listen_pcb, IP_ADDR_ANY, BRIDGE_PORT);
    /* 0x07: after tcp_bind */
    debug_leds_out_write(0x07);

    listen_pcb = tcp_listen(listen_pcb);
    if (!listen_pcb) {
        /* 0x0F: tcp_listen() returned NULL — halt with all debug LEDs on */
        debug_leds_out_write(0x0F);
        while (1) ;
    }
    /* 0x08: after tcp_listen */
    debug_leds_out_write(0x08);

    tcp_accept(listen_pcb, bridge_accept);

    /* Bits 0+1+3 (D6+D7+D9): init complete, listening */
    debug_leds_out_write(0x0B);

    while (1) {
        ethernetif_input(&netif);
        sys_check_timeouts();
    }

    return 0;
}
