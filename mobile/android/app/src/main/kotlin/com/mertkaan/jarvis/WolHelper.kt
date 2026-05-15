package com.mertkaan.jarvis

import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress

/**
 * Wake-on-LAN magic packet sender.
 *
 * A magic packet is 6 bytes of 0xFF followed by the target MAC address
 * repeated 16 times — 102 bytes total.
 */
object WolHelper {

    fun send(mac: String, broadcastAddress: String = "255.255.255.255", port: Int = 9) {
        val macBytes = parseMac(mac)
        val packet = buildMagicPacket(macBytes)

        DatagramSocket().use { socket ->
            socket.broadcast = true
            val address = InetAddress.getByName(broadcastAddress)
            val datagram = DatagramPacket(packet, packet.size, address, port)
            socket.send(datagram)
        }
    }

    private fun parseMac(mac: String): ByteArray {
        val hex = mac.replace(":", "").replace("-", "").replace(".", "")
        require(hex.length == 12) { "Invalid MAC address: $mac" }
        return ByteArray(6) { i ->
            hex.substring(i * 2, i * 2 + 2).toInt(16).toByte()
        }
    }

    private fun buildMagicPacket(macBytes: ByteArray): ByteArray {
        val packet = ByteArray(6 + 16 * 6)
        // First 6 bytes: 0xFF
        for (i in 0 until 6) packet[i] = 0xFF.toByte()
        // Repeat MAC 16 times
        for (i in 0 until 16) {
            System.arraycopy(macBytes, 0, packet, 6 + i * 6, 6)
        }
        return packet
    }
}
