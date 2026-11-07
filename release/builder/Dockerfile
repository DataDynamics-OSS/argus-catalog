# Argus Catalog 릴리스 빌더 — Rocky Linux 8 (glibc 2.28)
#
# 여기서 만든 wheel·RPM 은 EL8 과 EL9 양쪽에서 동작한다(EL9 의 glibc 가 더 새 버전이므로).
# 대상 Python 은 두 배포판 AppStream 에 모두 있는 python3.11 이다.
# 인터넷이 되는 빌드 머신에서만 사용한다 — 설치 대상(폐쇄망)에는 필요 없다.
ARG BASE_IMAGE=rockylinux:8
FROM ${BASE_IMAGE}

RUN dnf -y install \
        python3.11 python3.11-devel python3.11-pip \
        gcc gcc-c++ make \
        rpm-build \
        krb5-devel openssl-devel libffi-devel \
        git tar gzip xz findutils which rsync file \
    && dnf clean all

# 최신 pip 로 wheel 빌드(manylinux_2_28 태그 인식, PEP 517 빌드 안정성)
RUN python3.11 -m pip install --no-cache-dir --upgrade pip setuptools wheel build

WORKDIR /work
