# Copyright (C) 10se1ucgo 2015-2016
#
# This file is part of DisableWinTracking.
#
# DisableWinTracking is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# DisableWinTracking is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with DisableWinTracking.  If not, see <http://www.gnu.org/licenses/>.
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import winreg

import pywintypes
import win32serviceutil
import winerror

logger = logging.getLogger('dwt.util')


class CalledProcessError(Exception):
    # This exception is raised by subprocess_handler() returns a non-zero exit status.
    # It is a direct copy + paste backport from Python 3, as the Python 2 version does not
    # include the "stderr" property.
    #
    # Original docstring:
    #     This exception is raised when a process run by check_call() or
    #     check_output() returns a non-zero exit status.
    #     The exit status will be stored in the returncode attribute;
    #     check_output() will also store the output in the output attribute.

    def __init__(self, returncode, cmd, output=None, stderr=None):
        self.returncode = returncode
        self.cmd = cmd
        self.output = output
        self.stderr = stderr

    def __str__(self):
        return "Command '%s' returned non-zero exit status %d" % (self.cmd, self.returncode)

    @property
    def stdout(self):
        # Alias for output attribute, to match stderr
        return self.output

    @stdout.setter
    def stdout(self, value):
        # There's no obvious reason to set this, but allow it anyway so
        # .stdout is a transparent alias for .output
        self.output = value


def is_64bit():
    if os.name == 'nt':
        output = subprocess.check_output(['wmic', 'os', 'get', 'OSArchitecture'])
        os_arch = output.split()[1]
        return True if os_arch == '64-bit' else False
    else:
        logger.critical("This was only meant to be run on Windows-based system. Specifically, Windows 10.")
        os._exit(0)
    return os_arch


def ip_block(ip_list, undo):
    for ip in ip_list:
        cmd = 'netsh advfirewall firewall {act} rule name="TrackingIP-{ip}"'.format(act='delete' if undo else 'add',
                                                                                    ip=ip)
        if not undo:
            cmd += ' dir=out protocol=any remoteip="{ip}" profile=any action=block'.format(ip=ip)

        try:
            subprocess_handler(shlex.split(cmd))
            logger.info(
                "IP Blocker: The IP {ip} was successfully {act}.".format(ip=ip, act='unblocked' if undo else 'blocked'))
        except CalledProcessError as e:
            logger.exception("IP Blocker: Failed to {act} IP {ip}".format(act='unblock' if undo else 'block', ip=ip))
            logger.critical("IP Blocker: Error output:\n" + e.stdout.decode('ascii', 'replace'))


def clear_diagtrack():
    file = os.path.join(os.environ['SYSTEMDRIVE'],
                        '\\ProgramData\\Microsoft\\Diagnosis\\ETLLogs\\AutoLogger\\AutoLogger-Diagtrack-Listener.etl')

    cmds = ['sc delete DiagTrack',
            'sc delete dmwappushservice',
            'echo "" > "{file}"'.format(file=file)]

    i = 0
    failed = False
    for cmd in cmds:
        i += 1
        service = shlex.split(cmd, 'sc delete ')
        output = subprocess_handler(cmd)
        if output[0] in [0, 1060, 1072]:
            if output[0] == 0:
                if len(service) > 1:
                    logger.info("DiagTrack: Successfully deleted service '{0}'".format(service[1]))
                else:
                    logger.info("DiagTrack: Successfully erased tracking log.")
            if output[0] == 1060:
                logger.info("DiagTrack: {0} service doesn't exist. This is OK, you likely removed it already.".format(
                    service[1]))
            if output[0] == 1072:
                logger.info(
                    "DiagTrack: {0} service marked for deletion. This is OK, make sure you reboot your machine!".format(
                        service[1]))

            logger.info("DiagTrack: Completed Part {0}/{1}".format(i, len(cmds)))
        else:
            logger.info("{0}".format(output[0]))
            failed = True
            logger.exception("DiagTrack: Failed Part {0}/{1}".format(i, len(cmds)))
            logger.critical("DiagTrack: Error code: {0} - {1}".format(output[0], output[1]))

    if failed:
        logger.info("DiagTrack: Complete. Errors were recorded.")
    else:
        logger.info("DiagTrack: Completed successfully, without errors.")


def delete_service(service):
    try:
        win32serviceutil.RemoveService(service)
        logger.info("Services: Succesfully removed service '{service}'".format(service=service))
    except pywintypes.error as e:
        errors = (winerror.ERROR_SERVICE_DOES_NOT_EXIST, winerror.ERROR_SERVICE_NOT_ACTIVE)
        if not any(error == e.winerror for error in errors):
            logger.exception("Services: Failed to remove service '{service}'".format(service=service))


def disable_service(service):
    try:
        win32serviceutil.StopService(service)
        logger.info("Services: Succesfully stopped service '{service}'".format(service=service))
    except pywintypes.error as e:
        errors = (winerror.ERROR_SERVICE_DOES_NOT_EXIST, winerror.ERROR_SERVICE_NOT_ACTIVE)
        if not any(error == e.winerror for error in errors):
            logger.exception("Services: Failed to stop service '{service}'".format(service=service))


def telemetry(undo):
    value = int(undo)
    telemetry_keys = {'AllowTelemetry': [winreg.HKEY_LOCAL_MACHINE,
                                         r'SOFTWARE\Policies\Microsoft\Windows\DataCollection',
                                         "AllowTelemetry", winreg.REG_DWORD, value]}
    set_registry(telemetry_keys)


def services(undo):
    value = 4 if undo else 3
    service_keys = {'dmwappushsvc': [winreg.HKEY_LOCAL_MACHINE,
                                     r'SYSTEM\\CurrentControlSet\\Services\\dmwappushsvc',
                                     'Start', winreg.REG_DWORD, value],

                    'DiagTrack': [winreg.HKEY_LOCAL_MACHINE,
                                  r'SYSTEM\\CurrentControlSet\\Services\\DiagTrack',
                                  'Start', winreg.REG_DWORD, value]}
    set_registry(service_keys)


def defender(undo):
    value = int(undo)
    defender_keys = {'Windows Defender Delivery Optimization Download':
                         [winreg.HKEY_LOCAL_MACHINE,
                          r'SOFTWARE\Microsoft\Windows\CurrentVersion\DeliveryOptimization\Config',
                          'DODownloadMode', winreg.REG_DWORD, value],

                     'Windows Defender Spynet': [winreg.HKEY_LOCAL_MACHINE,
                                                 r'SOFTWARE\Microsoft\Windows Defender\Spynet',
                                                 'SpyNetReporting', winreg.REG_DWORD, value],

                     'Windows Defender Sample Submission': [winreg.HKEY_LOCAL_MACHINE,
                                                            r'SOFTWARE\Microsoft\Windows Defender\Spynet',
                                                            'SubmitSamplesConsent', winreg.REG_DWORD, value]}
    set_registry(defender_keys)


def wifisense(undo):
    value = int(undo)
    wifisense_keys = {'WifiSense Credential Share': [winreg.HKEY_LOCAL_MACHINE,
                                                     r'SOFTWARE\Microsoft\WcmSvc\wifinetworkmanager\features',
                                                     'WiFiSenseCredShared', winreg.REG_DWORD, value],

                      'WifiSense Open-ness': [winreg.HKEY_LOCAL_MACHINE,
                                              r'SOFTWARE\Microsoft\WcmSvc\wifinetworkmanager\features',
                                              'WiFiSenseOpen', winreg.REG_DWORD, value]}
    set_registry(wifisense_keys)


def onedrive(undo):
    file_sync_value = int(undo)
    list_pin_value = int(not undo)
    action = "install" if undo else "uninstall"

    if is_64bit():
        onedrive_keys = {'FileSync': [winreg.HKEY_LOCAL_MACHINE,
                                      r'SOFTWARE\Policies\Microsoft\Windows\OneDrive',
                                      'DisableFileSyncNGSC', winreg.REG_DWORD, file_sync_value],

                         'ListPin': [winreg.HKEY_CLASSES_ROOT,
                                     r'CLSID\{018D5C66-4533-4307-9B53-224DE2ED1FE6}',
                                     'System.IsPinnedToNameSpaceTree', winreg.REG_DWORD, list_pin_value],

                         'ListPin64Bit': [winreg.HKEY_CLASSES_ROOT,
                                          r'Wow6432Node\CLSID\{018D5C66-4533-4307-9B53-224DE2ED1FE6}',
                                          'System.IsPinnedToNameSpaceTree', winreg.REG_DWORD, list_pin_value]}
    else:
        onedrive_keys = {'FileSync': [winreg.HKEY_LOCAL_MACHINE,
                                      r'SOFTWARE\Policies\Microsoft\Windows\OneDrive',
                                      'DisableFileSyncNGSC', winreg.REG_DWORD, file_sync_value],

                         'ListPin': [winreg.HKEY_CLASSES_ROOT,
                                     r'CLSID\{018D5C66-4533-4307-9B53-224DE2ED1FE6}',
                                     'System.IsPinnedToNameSpaceTree', winreg.REG_DWORD, list_pin_value]}

    set_registry(onedrive_keys)

    system = "SysWOW64" if is_64bit() else "System32"
    onedrive_setup = os.path.join(os.environ['SYSTEMROOT'], "{system}\\OneDriveSetup.exe".format(system=system))
    cmd = "{bin} /{action}".format(bin=onedrive_setup, action=action)

    output = subprocess_handler(cmd)
    if output[0] == -2147219823:
        logger.info("OneDrive: successfully {action}ed".format(action=action))
    else:
        logger.info(
            "OneDrive: unable to {action}. Exited with code: {code} - {message}".format(action=action, code=output[0],
                                                                                        message=output[1]))


def set_registry(keys):
    mask = winreg.KEY_WOW64_64KEY | winreg.KEY_ALL_ACCESS if is_64bit() else winreg.KEY_ALL_ACCESS

    for key_name, values in keys.items():
        try:
            key = winreg.CreateKeyEx(values[0], values[1], 0, mask)
            winreg.SetValueEx(key, values[2], 0, values[3], values[4])
            winreg.CloseKey(key)
            logger.info("Registry: Successfully modified {key} key.".format(key=key_name))
        except OSError:
            logger.exception("Registry: Unable to modify {key} key.".format(key=key_name))


def clean_hosts():
    hosts_path = os.path.join(os.environ['SYSTEMROOT'], 'System32\\drivers\\etc')
    os.remove(hosts_path + '\\hosts')
    os.remove(hosts_path + '\\hosts.donotremove.bak')
    flush_dns()


def hosts_ad_removal(entries, undo):
    urllib.request.URLopener().retrieve("https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts", "hosts.txt")
    # urllib.request.URLopener().retrieve("http://someonewhocares.org/hosts/zero/hosts", "hosts.txt")
    null_ip = "0.0.0.0"
    www = "www."
    all_entries = [(null_ip + " ") + x for x in entries] + [(null_ip + " " + www) + x for x in entries]
    hosts_path = os.path.join(os.environ['SYSTEMROOT'], 'System32/drivers/etc/hosts')
    if undo:
        try:
            with open(hosts_path, 'a') as f:
                f.write('\n' + '\n'.join(all_entries))
            os.remove('hosts.txt')
            logger.info("Hosts: Successfully reverted adblocking")
            return True
        except (WindowsError, IOError):
            logger.exception("Hosts: Failed to undo hosts file")
    else:
        try:
            with open('hosts.txt', 'r') as hosts, tempfile.NamedTemporaryFile(delete=False, mode='w') as temp:
                for line in hosts:
                    if not any(domain in line for domain in all_entries):
                        temp.write(line)
                temp.close()
                shutil.move(temp.name, hosts_path)
            os.remove('hosts.txt')
            logger.info("Hosts: Successfully removed ads")
            return True
        except OSError:
            logger.exception("Hosts: Failed to modify hosts file")
    return False


def hosts_tracking_removal(entries, undo):
    null_ip = "0.0.0.0"
    nulled_entries = [(null_ip + " ") + x for x in entries]
    hosts_path = os.path.join(os.environ['SYSTEMROOT'], 'System32/drivers/etc/hosts')
    if undo:
        try:
            with open(hosts_path, 'r') as hosts, tempfile.NamedTemporaryFile(delete=False) as temp:
                for line in hosts:
                    if not any(domain in line for domain in entries):
                        temp.write(line)
                temp.close()
                shutil.move(temp.name, hosts_path)
            logger.info("Hosts: Successfully enabled tracking")
            return True
        except OSError:
            logger.exception("Hosts: Failed to undo hosts file")
    else:
        try:
            with open(hosts_path, 'a') as f:
                f.write('\n' + '\n'.join(nulled_entries))
            logger.info("Hosts: Successfully disabled tracking")
            return True
        except (WindowsError, IOError):
            logger.exception("Hosts: Failed to modify hosts file")

    return False


def windows_update(undo):
    if undo:
        try:
            os.system('sc config BITS start=auto > NUL')
            os.system('sc config DoSvc start=auto > NUL')
            os.system('sc config wuauserv start=auto > NUL')
            os.system('sc config UsoSvc start=auto > NUL')
            os.system('net start BITS > NUL')
            os.system('net start DoSvc > NUL')
            os.system('net start wuauserv > NUL')
            os.system('net start UsoSvc > NUL')
            os.system('wuauclt /updatenow > NUL')
            logger.info("Windows Update: Updates are now enabled")
            return True
        except OSError:
            logger.exception("Windows Update: Failed to enable updates")
    else:
        try:
            os.system('sc config BITS start=disabled > NUL')
            os.system('sc config DoSvc start=disabled > NUL')
            os.system('sc config wuauserv start=disabled > NUL')
            os.system('sc config UsoSvc start=disabled > NUL')
            os.system('net stop BITS > NUL')
            os.system('net stop DoSvc > NUL')
            os.system('net stop wuauserv > NUL')
            os.system('net stop UsoSvc > NUL')
            logger.info("Windows Update: Updates are now disabled")
            return True
        except OSError:
            logger.exception("Windows Update: Failed to disable updates")
    return False


def cloudflare_dns(undo):
    if undo:
        try:
            os.system('wmic nicconfig where (IPEnabled=TRUE and DHCPEnabled=TRUE) call SetDNSServerSearchOrder() > NUL')
            logger.info("DNS: Removed CloudFlare DNS Servers")
            flush_dns()
            return True
        except OSError:
            logger.info("DNS: Failed to remove CloudFlare DNS Servers")
    else:
        try:
            os.system('wmic nicconfig where (IPEnabled=TRUE and DHCPEnabled=TRUE) call SetDNSServerSearchOrder('
                      '"1.1.1.1", "1.0.0.1") > NUL')
            logger.info("DNS: Applied CloudFlare DNS Servers")
            flush_dns()
            return True
        except OSError:
            logger.info("DNS: Failed to apply CloudFlare DNS Servers")
    return False


def flush_dns():
    os.system('ipconfig /flushdns > NUL')


def app_manager(apps):
    running = {}
    for app in apps:
        cmd = 'powershell "Get-AppxPackage *{app}*|Remove-AppxPackage"'.format(app=app)
        try:
            process = subprocess.Popen(shlex.split(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       stdin=subprocess.PIPE)
            running[app] = process
        except OSError:
            logger.exception("App remover: Failed to remove app '{app}'".format(app=app))

    for app, process in running.items():
        process.wait()
        if process.returncode:
            logger.exception("App remover: Failed to remove app '{app}'".format(app=app))
        else:
            logger.info("Successfully removed app '{app}'".format(app=app))


def subprocess_handler(cmd):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE, shell=True)
    output = p.communicate()

    return [p.returncode, output]


def log_cleanup():
    try:
        os.remove('dwt.log')
    except (OSError, IOError):
        logger.exception("Could not remove log file.")
