#!/usr/bin/env perl
# run_hard_checks.pl — run_hard_checks.py 的 perl 平替（无 python3 的镜像用，
# 如 R 系任务镜像只有 R/perl，2026-09-18 adaptive-rejection 实测）。
# 输出与 python 版相同的 JSON：逐条 check 跑 bash -c，记录 rc/stdout/stderr
# （截断 20000 字节）。
use strict;
use warnings;

my ($checks_path, $out_path) = @ARGV;
die "usage: run_hard_checks.pl <checks.json> <out.json>\n" unless $checks_path && $out_path;

my $MAX_CAPTURE = 20000;

local $/;
open my $fh, '<', $checks_path or die "open $checks_path: $!";
my $spec = <$fh>;
close $fh;

my ($instance_id) = $spec =~ /"instance_id"\s*:\s*"((?:[^"\\]|\\.)*)"/;
$instance_id = defined $instance_id ? $instance_id : "";

# 先定位 checks 数组起点，再深度计数切出数组内每个 {...} 对象
# （字符串内的大括号不计深度）
my @objs;
{
    my $base = index($spec, '"checks"');
    $base = index($spec, '[', $base) if $base >= 0;
    $base = 0 if $base < 0;
    my ($depth, $start, $in_str, $esc) = (0, -1, 0, 0);
    my @chars = split //, substr($spec, $base);
    for my $i (0 .. $#chars) {
        my $c = $chars[$i];
        if ($in_str) {
            if ($esc) { $esc = 0 }
            elsif ($c eq "\\") { $esc = 1 }
            elsif ($c eq '"') { $in_str = 0 }
            next;
        }
        if ($c eq '"') { $in_str = 1; next }
        if ($c eq '{') {
            $start = $i if $depth == 0;
            $depth++;
        } elsif ($c eq '}') {
            $depth--;
            if ($depth == 0 && $start >= 0) {
                # [2026-09-19 fix] off-by-$base bug: $start/$i 是相对 substr($spec,$base) 的偏移
                push @objs, substr($spec, $base + $start, $i - $start + 1);
                $start = -1;
            }
        }
    }
}

sub jget_str {
    my ($obj, $key) = @_;
    if ($obj =~ /"\Q$key\E"\s*:\s*"((?:[^"\\]|\\.)*)"/s) {
        my $v = $1;
        $v =~ s/\\(["\\\/bfnrt])/$1 eq 'b' ? "\b" : $1 eq 'f' ? "\f" : $1 eq 'n' ? "\n" : $1 eq 'r' ? "\r" : $1 eq 't' ? "\t" : $1/eg;
        return $v;
    }
    return "";
}
sub jget_bool {
    my ($obj, $key) = @_;
    return ($obj =~ /"\Q$key\E"\s*:\s*true/s) ? 1 : 0;
}
sub jesc {
    my ($v) = @_;
    $v = '' unless defined $v;
    $v =~ s/\\/\\\\/g; $v =~ s/"/\\"/g;
    $v =~ s/\n/\\n/g; $v =~ s/\r/\\r/g; $v =~ s/\t/\\t/g;
    return $v;
}
sub jesc_raw {  # 不反转义回 \n，只转义 \ 和 "（cmd/expect 按原文回写）
    my ($v) = @_;
    $v = '' unless defined $v;
    $v =~ s/\\/\\\\/g; $v =~ s/"/\\"/g;
    return $v;
}

my $now = do {
    my @t = gmtime;
    sprintf "%04d-%02d-%02dT%02d:%02d:%02d+00:00",
        $t[5] + 1900, $t[4] + 1, $t[3], $t[2], $t[1], $t[0];
};

my @results;
for my $obj (@objs) {
    next unless $obj =~ /"cmd"\s*:/;
    my $cmd  = jget_str($obj, "cmd");
    my $dim  = jget_str($obj, "dimension");
    my $cid  = jget_str($obj, "id");
    my $exp  = jget_str($obj, "expect");
    my $anz  = jget_bool($obj, "allow_nonzero") ? "true" : "false";
    my ($cmd_raw, $exp_raw) = ("", "");
    ($cmd_raw) = $obj =~ /"cmd"\s*:\s*"((?:[^"\\]|\\.)*)"/s;
    ($exp_raw) = $obj =~ /"expect"\s*:\s*"((?:[^"\\]|\\.)*)"/s;

    my $out_file = "/tmp/.hc_out_$$"; my $err_file = "/tmp/.hc_err_$$";
    my $rc = system("timeout 120 bash -c " . shell_quote($cmd) . " >$out_file 2>$err_file");
    $rc = $rc >> 8;
    my $stdout = slurp($out_file); my $stderr = slurp($err_file);
    unlink $out_file, $err_file;
    $stdout = substr($stdout, 0, $MAX_CAPTURE);
    $stderr = substr($stderr, 0, $MAX_CAPTURE);

    push @results,
        '{"dimension":"' . jesc($dim) . '","id":"' . jesc($cid) .
        '","cmd":"' . jesc_raw($cmd_raw) . '","expect":"' . jesc_raw($exp_raw) .
        '","allow_nonzero":' . $anz . ',"exit_code":' . $rc .
        ',"stdout":"' . jesc($stdout) . '","stderr":"' . jesc($stderr) . '"}';
}
sub shell_quote { my ($s) = @_; $s =~ s/'/'\\''/g; return "'$s'" }
sub slurp { my ($p) = @_; return "" unless -f $p; open my $f, '<', $p or return ""; local $/; my $d = <$f>; close $f; return defined $d ? $d : "" }

open my $of, '>', $out_path or die "open $out_path: $!";
print $of '{"instance_id":"' . jesc($instance_id) . '","collected_at":"' . $now .
    '","results":[' . join(",", @results) . "]}";
close $of;
