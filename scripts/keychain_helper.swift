#!/usr/bin/env swift

import Foundation
import Security

enum HelperError: Error {
    case usage
    case invalidUTF8
    case keychain(OSStatus)
}

func query(service: String, account: String) -> [CFString: Any] {
    return [
        kSecClass: kSecClassGenericPassword,
        kSecAttrService: service,
        kSecAttrAccount: account,
    ]
}

func writeError(_ message: String) {
    FileHandle.standardError.write(Data((message + "\n").utf8))
}

func setSecret(service: String, account: String) throws {
    let data = FileHandle.standardInput.readDataToEndOfFile()
    guard !data.isEmpty else {
        writeError("empty secret")
        exit(2)
    }
    let base = query(service: service, account: account)
    let status = SecItemCopyMatching(base as CFDictionary, nil)
    if status == errSecSuccess {
        let update: [CFString: Any] = [kSecValueData: data]
        let updateStatus = SecItemUpdate(base as CFDictionary, update as CFDictionary)
        guard updateStatus == errSecSuccess else { throw HelperError.keychain(updateStatus) }
    } else if status == errSecItemNotFound {
        var add = base
        add[kSecValueData] = data
        add[kSecAttrAccessible] = kSecAttrAccessibleAfterFirstUnlock
        let addStatus = SecItemAdd(add as CFDictionary, nil)
        guard addStatus == errSecSuccess else { throw HelperError.keychain(addStatus) }
    } else {
        throw HelperError.keychain(status)
    }
}

func getSecret(service: String, account: String) throws {
    var lookup = query(service: service, account: account)
    lookup[kSecReturnData] = true
    lookup[kSecMatchLimit] = kSecMatchLimitOne
    var result: CFTypeRef?
    let status = SecItemCopyMatching(lookup as CFDictionary, &result)
    if status == errSecItemNotFound { exit(2) }
    guard status == errSecSuccess, let data = result as? Data else {
        throw HelperError.keychain(status)
    }
    FileHandle.standardOutput.write(data)
}

func deleteSecret(service: String, account: String) throws {
    let status = SecItemDelete(query(service: service, account: account) as CFDictionary)
    if status == errSecItemNotFound { exit(2) }
    guard status == errSecSuccess else { throw HelperError.keychain(status) }
}

guard CommandLine.arguments.count == 4 else {
    writeError("usage: keychain_helper.swift <set|get|delete> <service> <account>")
    exit(64)
}

let command = CommandLine.arguments[1]
let service = CommandLine.arguments[2]
let account = CommandLine.arguments[3]

do {
    switch command {
    case "set": try setSecret(service: service, account: account)
    case "get": try getSecret(service: service, account: account)
    case "delete": try deleteSecret(service: service, account: account)
    default: throw HelperError.usage
    }
} catch HelperError.keychain(let status) {
    let detail = SecCopyErrorMessageString(status, nil) as String? ?? "unknown keychain error"
    writeError("keychain error \(status): \(detail)")
    exit(1)
} catch {
    writeError("keychain helper failed")
    exit(1)
}
