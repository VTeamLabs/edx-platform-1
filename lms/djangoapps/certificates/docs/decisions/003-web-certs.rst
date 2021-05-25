Web (HTML) course certificates
==============================

Status
------
Accepted

Background
----------
edX-platform currently has code for both PDF and web (or HTML) course
certificates. Over time, much of the PDF certificate code has been deprecated
or disabled and it will likely be removed in a future release.

It is strongly suggested that all course runs that use course certificates be
configured to use web certificates.

Configuring web certificates
----------------------------
To configure web certificates, follow the documentation to `Enable Course
Certificates`_ and `Set Up Certificates in Studio`_.

In particular, the following things must be true in order for a web certificate
to be viewable:

* The *CERTIFICATES_HTML_VIEW* feature is globally enabled
* The course run has web certificates enabled
* The course run has at least 1 certificate created and activated

To enable the *CERTIFICATES_HTML_VIEW* feature, follow the instructions to
`Enable Course Certificates`_.

To ensure that a course run has web certificates enabled, follow the
instructions to `Set Up Certificates in Studio`_. In particular, follow the
steps to *Enable a Certificate*. This will result in the course run's course
overview having *cert_html_view_enabled* set to True

To ensure that a course run has at least 1 certificate created and activated,
follow the instructions to `Set Up Certificates in Studio`_. In particular,
follow the steps to *Create a Certificate* and to *Activate a Certificate*.
This will result in the course run's course overview having
*has_any_active_web_certificate* set to True

To find any course runs with downloadable certificates that might need to be
updated, you can run a query similar to this:

.. code-block:: SQL

    select distinct
        cert.course_id
    from
       CERTIFICATES_GENERATEDCERTIFICATE as cert
    join
        COURSE_OVERVIEWS_COURSEOVERVIEW as overview
    on
        cert.course_id = overview.id
    where
        cert.status = 'downloadable'
    and
    (
        overview.cert_html_view_enabled = False
    or
        overview.has_any_active_web_certificate = False
    )
    order by
        cert.course_id

Viewing a certificate
---------------------
Depending on when a downloadable course certificate was created and the
certificate configuration for its associated course run, the certificate may
have a viewable web certificate, a PDF certificate, both, or neither.

To view a downloadable certificate, first determine your site's base URL. For
edX, this is ``https://courses.edx.org/``

Next, find the desired certificate in the CERTIFICATES_GENERATEDCERTIFICATE
database table.

To view the web certificate, find the *verify_uuid* from the table, then
construct a URL by appending */certificates/verify_uuid* to the base URL.
For example, the URL for edX will look like this:
``https://courses.edx.org/certificates/verify_uuid``

The web certificate may not be viewable if the course run is not
properly configured for web certificates. If this is the case, follow the
instructions above to configure the course run for web certificates.

To view the PDF certificate, find the *download_url* from the table, then
navigate to that URL in your browser. The PDF certificate may not exist
(the *download_url* may be an empty string) if the certificate was created
after PDF certificate generation was disabled. If this is the case,
edx-platform does not contain any supported code to create the PDF.

References
------------

Documentation for enabling course certificates:

* `Enable Course Certificates`_
* `Set Up Certificates in Studio`_

Some PRs that deprecated or disabled PDF certificates:

* `Disable PDF certificate generation`_
* `Deprecate web certificate setting`_

.. _Enable Course Certificates: https://edx.readthedocs.io/projects/edx-installing-configuring-and-running/en/latest/configuration/enable_certificates.html
.. _Deprecate web certificate setting: https://github.com/edx/edx-platform/pull/17285
.. _Disable PDF certificate generation: https://github.com/edx/edx-platform/pull/19833
.. _Set Up Certificates in Studio: https://edx.readthedocs.io/projects/open-edx-building-and-running-a-course/en/latest/set_up_course/studio_add_course_information/studio_creating_certificates.html

