run_ntp_test('~/repositories/Psychtoolbox-3/Psychtoolbox/PsychHardware/')

function run_ntp_test(path)
addpath(path)
IP = '10.10.10.42'
port = '55513'
NTP_IP = '10.10.10.51'
[err, msg] = NetStation('Connect', IP, port)
[err, msg] = NetStation('getntpsynchronize', NTP_IP)
[err, msg] = NetStation('StartRecording')
NetStation('Event')
for i = 1:10
    pause(3)
    [err, msg] = NetStation('Event')
end

[err, msg] = NetStation('StopRecording')
[err, msg] = NetStation('Disconnect')
end
